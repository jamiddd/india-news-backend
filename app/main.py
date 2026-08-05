import asyncio
from contextlib import asynccontextmanager
from typing import Optional, List
from fastapi import FastAPI, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy import desc, or_

from app.config import settings
from app.database import engine, Base, get_db
from app.models import Source, Article, StoryCluster, User, utc_now
from app.schemas import (
    SourceOut, StoryClusterOut, ArticleOut, PaginatedClustersOut,
    UserAuthRequest, UserAuthResponse, UserPreferences,
)
from uuid import uuid4
from app.services.poller import poll_all_sources
from app.services.enrichment import enrich_cluster_with_ai
from app.services.google_oauth import verify_google_id_token, InvalidGoogleIdToken

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan
)

DEFAULT_PREFERENCES = UserPreferences(
    enabled_categories=["all", "national", "business", "official", "sports", "entertainment", "tech", "politics"]
)


@app.post(f"{settings.API_V1_STR}/auth/login", response_model=UserAuthResponse)
async def login_user(payload: UserAuthRequest, db: AsyncSession = Depends(get_db)):
    """
    Create/update a user and return the preferences saved for that account.

    For provider="google", `payload.uid` is the Google ID token (a signed JWT,
    not a stable identifier) — it must be cryptographically verified against
    Google's own keys before trusting the email/name it claims, and the
    stable identity to key the user row on is the token's "sub" claim, not
    the token string itself (which is different on every login).
    """
    verified_email = payload.email
    verified_name = payload.display_name
    provider_uid = payload.uid

    if payload.provider == "google":
        if not payload.uid:
            raise HTTPException(status_code=401, detail="Missing Google ID token")
        if not settings.GOOGLE_OAUTH_CLIENT_ID:
            raise HTTPException(status_code=500, detail="Google OAuth client ID not configured on server")

        try:
            identity = await verify_google_id_token(payload.uid, settings.GOOGLE_OAUTH_CLIENT_ID)
        except InvalidGoogleIdToken as e:
            raise HTTPException(status_code=401, detail=f"Invalid Google ID token: {e}")

        verified_email = identity.email
        verified_name = identity.name
        provider_uid = identity.subject  # stable per-account id, not the token itself

    lookup = (
        select(User).where(User.provider == payload.provider, User.provider_uid == provider_uid)
        if provider_uid else select(User).where(User.email == verified_email)
    )
    result = await db.execute(lookup)
    user = result.scalar_one_or_none()

    if user is None:
        user = User(
            id=f"usr_{uuid4().hex[:12]}",
            email=verified_email,
            display_name=verified_name,
            provider=payload.provider,
            provider_uid=provider_uid,
            preferences=DEFAULT_PREFERENCES.model_dump(),
        )
        db.add(user)
    else:
        user.email = verified_email
        user.display_name = verified_name
        user.provider = payload.provider
        user.provider_uid = provider_uid
        user.updated_at = utc_now()

    await db.commit()
    await db.refresh(user)
    return UserAuthResponse(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        preferences=UserPreferences.model_validate(user.preferences or {}),
    )


@app.put(f"{settings.API_V1_STR}/users/{{user_id}}/preferences", status_code=200)
async def update_user_preferences(
    user_id: str,
    preferences: UserPreferences,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    user.preferences = preferences.model_dump()
    user.updated_at = utc_now()
    await db.commit()
    return {"message": "Preferences updated successfully"}

@app.get("/")
async def root():
    return {
        "status": "healthy",
        "app": settings.PROJECT_NAME,
        "version": settings.VERSION
    }

@app.get(f"{settings.API_V1_STR}/sources", response_model=List[SourceOut])
async def list_sources(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Source))
    return result.scalars().all()

@app.post(f"{settings.API_V1_STR}/ingest/poll")
async def trigger_poll(background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    background_tasks.add_task(poll_all_sources, db)
    return {"message": "Ingestion polling triggered in background."}

@app.post(f"{settings.API_V1_STR}/clusters/{{cluster_id}}/enrich")
async def enrich_cluster_endpoint(cluster_id: int, db: AsyncSession = Depends(get_db)):
    query = (
        select(StoryCluster)
        .where(StoryCluster.id == cluster_id)
        .options(selectinload(StoryCluster.articles).selectinload(Article.source))
    )
    result = await db.execute(query)
    cluster = result.scalar_one_or_none()
    if not cluster:
        raise HTTPException(status_code=404, detail="Story cluster not found")

    enriched = await enrich_cluster_with_ai(db, cluster)
    return {"message": "Cluster enriched successfully", "data": enriched}

@app.get(f"{settings.API_V1_STR}/search", response_model=PaginatedClustersOut)
async def search_story_clusters(
    q: str = Query(..., min_length=2, description="Search query string across headlines and summaries"),
    limit: int = Query(20, ge=1, le=50),
    cursor: Optional[int] = Query(None, description="Cursor for pagination"),
    db: AsyncSession = Depends(get_db)
):
    pattern = f"%{q}%"
    query = (
        select(StoryCluster)
        .options(selectinload(StoryCluster.articles).selectinload(Article.source))
        .where(
            or_(
                StoryCluster.headline.ilike(pattern),
                StoryCluster.summary.ilike(pattern)
            )
        )
        .order_by(desc(StoryCluster.last_updated_at), desc(StoryCluster.id))
    )

    if cursor:
        query = query.where(StoryCluster.id < cursor)

    query = query.limit(limit + 1)
    result = await db.execute(query)
    clusters = result.scalars().all()

    has_more = len(clusters) > limit
    items = clusters[:limit]
    next_cursor = str(items[-1].id) if has_more and items else None

    formatted_clusters = [
        StoryClusterOut(
            id=cluster.id,
            headline=cluster.headline,
            summary=cluster.summary,
            article_count=cluster.article_count,
            first_seen_at=cluster.first_seen_at,
            last_updated_at=cluster.last_updated_at,
            entities=cluster.entities,
            topics=cluster.topics,
            framing_comparison=cluster.framing_comparison,
            articles=[
                ArticleOut(
                    id=art.id,
                    source_id=art.source_id,
                    source_name=art.source.name if art.source else "Unknown",
                    url=art.url,
                    title=art.title,
                    snippet=art.snippet,
                    content=art.content,
                    author=art.author,
                    published_at=art.published_at,
                    image_url=art.image_url
                )
                for art in cluster.articles
            ]
        )
        for cluster in items
    ]

    return PaginatedClustersOut(
        items=formatted_clusters,
        next_cursor=next_cursor,
        has_more=has_more
    )

@app.get(f"{settings.API_V1_STR}/clusters", response_model=PaginatedClustersOut)
async def list_story_clusters(
    category: Optional[str] = Query(None, description="Category filter (national, business, official, northeast)"),
    limit: int = Query(20, ge=1, le=50),
    cursor: Optional[int] = Query(None, description="Cursor for pagination"),
    db: AsyncSession = Depends(get_db)
):
    query = (
        select(StoryCluster)
        .options(selectinload(StoryCluster.articles).selectinload(Article.source))
        .order_by(desc(StoryCluster.last_updated_at), desc(StoryCluster.id))
    )

    if category and category.lower() != "all":
        subquery = (
            select(Article.cluster_id)
            .join(Source)
            .where(Source.category == category.lower())
        )
        query = query.where(StoryCluster.id.in_(subquery))

    if cursor:
        query = query.where(StoryCluster.id < cursor)

    query = query.limit(limit + 1)
    result = await db.execute(query)
    clusters = result.scalars().all()

    has_more = len(clusters) > limit
    items = clusters[:limit]
    next_cursor = str(items[-1].id) if has_more and items else None

    formatted_clusters = []
    seen_ids = set()
    for cluster in items:
        if cluster.id in seen_ids:
            continue
        seen_ids.add(cluster.id)

        articles_out = [
            ArticleOut(
                id=art.id,
                source_id=art.source_id,
                source_name=art.source.name if art.source else "Unknown",
                url=art.url,
                title=art.title,
                snippet=art.snippet,
                content=art.content,
                author=art.author,
                published_at=art.published_at,
                image_url=art.image_url
            )
            for art in cluster.articles
        ]
        
        formatted_clusters.append(
            StoryClusterOut(
                id=cluster.id,
                headline=cluster.headline,
                summary=cluster.summary,
                article_count=cluster.article_count,
                first_seen_at=cluster.first_seen_at,
                last_updated_at=cluster.last_updated_at,
                entities=cluster.entities,
                topics=cluster.topics,
                framing_comparison=cluster.framing_comparison,
                articles=articles_out
            )
        )

    return PaginatedClustersOut(
        items=formatted_clusters,
        next_cursor=next_cursor,
        has_more=has_more
    )

@app.get(f"{settings.API_V1_STR}/clusters/{{cluster_id}}", response_model=StoryClusterOut)
async def get_story_cluster(cluster_id: int, db: AsyncSession = Depends(get_db)):
    query = (
        select(StoryCluster)
        .where(StoryCluster.id == cluster_id)
        .options(selectinload(StoryCluster.articles).selectinload(Article.source))
    )
    result = await db.execute(query)
    cluster = result.scalar_one_or_none()

    if not cluster:
        raise HTTPException(status_code=404, detail="Story cluster not found")

    articles_out = [
        ArticleOut(
            id=art.id,
            source_id=art.source_id,
            source_name=art.source.name if art.source else "Unknown",
            url=art.url,
            title=art.title,
            snippet=art.snippet,
            content=art.content,
            author=art.author,
            published_at=art.published_at,
            image_url=art.image_url
        )
        for art in cluster.articles
    ]

    return StoryClusterOut(
        id=cluster.id,
        headline=cluster.headline,
        summary=cluster.summary,
        article_count=cluster.article_count,
        first_seen_at=cluster.first_seen_at,
        last_updated_at=cluster.last_updated_at,
        entities=cluster.entities,
        topics=cluster.topics,
        framing_comparison=cluster.framing_comparison,
        articles=articles_out
    )
