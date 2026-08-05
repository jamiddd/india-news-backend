import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Optional, List
from fastapi import FastAPI, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy import desc, or_, func

from app.config import settings
from app.database import engine, Base, get_db
from app.models import Source, Article, StoryCluster, User, CommunityPost, CommunityPostReview, CommunityPostReport, utc_now
from app.schemas import (
    SourceOut, StoryClusterOut, ArticleOut, PaginatedClustersOut,
    UserAuthRequest, UserAuthResponse, UserPreferences,
    CommunityPostCreate, CommunityPostOut, CommunityPostModeration, CommunityPostUpdate,
    CommunityPostReportCreate, CommunityPostReportOut,
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

def _emails(value: str) -> set[str]:
    return {item.strip().lower() for item in value.split(",") if item.strip()}

async def _community_user(user_id: str, db: AsyncSession, admin: bool = False) -> User:
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    allowed = _emails(settings.COMMUNITY_ADMIN_EMAILS if admin else settings.COMMUNITY_ALLOWED_EMAILS)
    if user is None or user.email.lower() not in allowed:
        raise HTTPException(status_code=403, detail="Community access is not enabled for this account")
    return user

def _community_out(post: CommunityPost) -> CommunityPostOut:
    return CommunityPostOut(
        id=post.id, author_id=post.author_id,
        author_display_name=post.author.display_name,
        title=post.title, body=post.body, category=post.category,
        image_urls=post.image_urls or [], status=post.status,
        rejection_reason=post.rejection_reason, created_at=post.created_at,
        updated_at=post.updated_at, published_at=post.published_at,
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


@app.get(f"{settings.API_V1_STR}/community/categories")
async def community_categories(user_id: str = Query(...), db: AsyncSession = Depends(get_db)):
    await _community_user(user_id, db)
    return {"items": ["national", "business", "politics", "technology", "local", "other"]}


@app.post(f"{settings.API_V1_STR}/community/posts", response_model=CommunityPostOut, status_code=201)
async def create_community_post(payload: CommunityPostCreate, db: AsyncSession = Depends(get_db)):
    user = await _community_user(payload.user_id, db)
    since = utc_now() - timedelta(hours=24)
    recent = await db.execute(select(func.count(CommunityPost.id)).where(CommunityPost.author_id == user.id, CommunityPost.created_at >= since))
    if recent.scalar_one() >= 10:
        raise HTTPException(status_code=429, detail="Daily community submission limit reached")
    post = CommunityPost(
        author_id=user.id, title=payload.title.strip(), body=payload.body.strip(),
        category=payload.category.strip().lower(), image_urls=payload.image_urls,
        status="PENDING_REVIEW" if payload.submit_for_review else "DRAFT",
    )
    db.add(post)
    await db.commit()
    await db.refresh(post, ["author"])
    return _community_out(post)


@app.get(f"{settings.API_V1_STR}/community/my-posts", response_model=List[CommunityPostOut])
async def my_community_posts(user_id: str = Query(...), db: AsyncSession = Depends(get_db)):
    user = await _community_user(user_id, db)
    result = await db.execute(
        select(CommunityPost).where(CommunityPost.author_id == user.id)
        .options(selectinload(CommunityPost.author))
        .order_by(desc(CommunityPost.updated_at))
    )
    return [_community_out(post) for post in result.scalars().all()]


@app.put(f"{settings.API_V1_STR}/community/posts/{{post_id}}", response_model=CommunityPostOut)
async def update_rejected_community_post(post_id: int, payload: CommunityPostUpdate, db: AsyncSession = Depends(get_db)):
    user = await _community_user(payload.user_id, db)
    result = await db.execute(select(CommunityPost).where(CommunityPost.id == post_id).options(selectinload(CommunityPost.author)))
    post = result.scalar_one_or_none()
    if post is None or post.author_id != user.id or post.status not in {"DRAFT", "REJECTED"}:
        raise HTTPException(status_code=404, detail="Editable community post not found")
    post.title, post.body = payload.title.strip(), payload.body.strip()
    post.category, post.image_urls = payload.category.strip().lower(), payload.image_urls
    post.status, post.rejection_reason = ("PENDING_REVIEW" if payload.submit_for_review else "DRAFT"), None
    post.reviewed_by, post.reviewed_at, post.published_at = None, None, None
    await db.commit()
    return _community_out(post)


@app.delete(f"{settings.API_V1_STR}/community/posts/{{post_id}}")
async def withdraw_community_post(post_id: int, user_id: str = Query(...), db: AsyncSession = Depends(get_db)):
    user = await _community_user(user_id, db)
    result = await db.execute(select(CommunityPost).where(CommunityPost.id == post_id, CommunityPost.author_id == user.id))
    post = result.scalar_one_or_none()
    if post is None or post.status not in {"DRAFT", "PENDING_REVIEW", "REJECTED"}:
        raise HTTPException(status_code=404, detail="Withdrawable community post not found")
    await db.delete(post)
    await db.commit()
    return {"message": "Community post withdrawn"}


@app.get(f"{settings.API_V1_STR}/community/posts", response_model=List[CommunityPostOut])
async def approved_community_posts(
    user_id: str = Query(...), category: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    await _community_user(user_id, db)
    query = select(CommunityPost).where(CommunityPost.status == "APPROVED")\
        .options(selectinload(CommunityPost.author)).order_by(desc(CommunityPost.published_at))
    if category and category.lower() != "all":
        query = query.where(CommunityPost.category == category.lower())
    result = await db.execute(query)
    return [_community_out(post) for post in result.scalars().all()]


@app.post(f"{settings.API_V1_STR}/community/posts/{{post_id}}/reports", status_code=201)
async def report_community_post(post_id: int, payload: CommunityPostReportCreate, db: AsyncSession = Depends(get_db)):
    user = await _community_user(payload.user_id, db)
    since = utc_now() - timedelta(hours=24)
    recent = await db.execute(select(func.count(CommunityPostReport.id)).where(CommunityPostReport.reporter_id == user.id, CommunityPostReport.created_at >= since))
    if recent.scalar_one() >= 20:
        raise HTTPException(status_code=429, detail="Daily report limit reached")
    result = await db.execute(select(CommunityPost).where(CommunityPost.id == post_id, CommunityPost.status == "APPROVED"))
    post = result.scalar_one_or_none()
    if post is None:
        raise HTTPException(status_code=404, detail="Approved community post not found")
    existing = await db.execute(select(CommunityPostReport).where(CommunityPostReport.post_id == post_id, CommunityPostReport.reporter_id == user.id))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="You have already reported this post")
    db.add(CommunityPostReport(post_id=post_id, reporter_id=user.id, reason=payload.reason.strip().lower(), details=payload.details))
    await db.commit()
    return {"message": "Report submitted for admin review"}


@app.get(f"{settings.API_V1_STR}/community/admin/posts", response_model=List[CommunityPostOut])
async def moderation_queue(admin_user_id: str = Query(...), db: AsyncSession = Depends(get_db)):
    await _community_user(admin_user_id, db, admin=True)
    result = await db.execute(
        select(CommunityPost).where(CommunityPost.status == "PENDING_REVIEW")
        .options(selectinload(CommunityPost.author)).order_by(CommunityPost.created_at)
    )
    return [_community_out(post) for post in result.scalars().all()]


@app.get(f"{settings.API_V1_STR}/community/admin/reports", response_model=List[CommunityPostReportOut])
async def community_reports(admin_user_id: str = Query(...), db: AsyncSession = Depends(get_db)):
    await _community_user(admin_user_id, db, admin=True)
    result = await db.execute(select(CommunityPostReport).order_by(desc(CommunityPostReport.created_at)))
    return result.scalars().all()


@app.post(f"{settings.API_V1_STR}/community/posts/{{post_id}}/approve", response_model=CommunityPostOut)
async def approve_community_post(post_id: int, payload: CommunityPostModeration, db: AsyncSession = Depends(get_db)):
    admin = await _community_user(payload.admin_user_id, db, admin=True)
    result = await db.execute(select(CommunityPost).where(CommunityPost.id == post_id).options(selectinload(CommunityPost.author)))
    post = result.scalar_one_or_none()
    if post is None or post.status != "PENDING_REVIEW":
        raise HTTPException(status_code=404, detail="Pending community post not found")
    post.status = "APPROVED"
    post.reviewed_by = admin.id
    post.reviewed_at = utc_now()
    post.published_at = utc_now()
    post.rejection_reason = None
    db.add(CommunityPostReview(post_id=post.id, admin_id=admin.id, action="APPROVED"))
    await db.commit()
    return _community_out(post)


@app.post(f"{settings.API_V1_STR}/community/posts/{{post_id}}/reject", response_model=CommunityPostOut)
async def reject_community_post(post_id: int, payload: CommunityPostModeration, db: AsyncSession = Depends(get_db)):
    if not payload.rejection_reason or not payload.rejection_reason.strip():
        raise HTTPException(status_code=422, detail="A rejection reason is required")
    admin = await _community_user(payload.admin_user_id, db, admin=True)
    result = await db.execute(select(CommunityPost).where(CommunityPost.id == post_id).options(selectinload(CommunityPost.author)))
    post = result.scalar_one_or_none()
    if post is None or post.status != "PENDING_REVIEW":
        raise HTTPException(status_code=404, detail="Pending community post not found")
    post.status = "REJECTED"
    post.reviewed_by = admin.id
    post.reviewed_at = utc_now()
    post.rejection_reason = payload.rejection_reason.strip()
    db.add(CommunityPostReview(post_id=post.id, admin_id=admin.id, action="REJECTED", reason=post.rejection_reason))
    await db.commit()
    return _community_out(post)


@app.post(f"{settings.API_V1_STR}/community/posts/{{post_id}}/takedown", response_model=CommunityPostOut)
async def takedown_community_post(post_id: int, payload: CommunityPostModeration, db: AsyncSession = Depends(get_db)):
    if not payload.rejection_reason or not payload.rejection_reason.strip():
        raise HTTPException(status_code=422, detail="A takedown reason is required")
    admin = await _community_user(payload.admin_user_id, db, admin=True)
    result = await db.execute(select(CommunityPost).where(CommunityPost.id == post_id).options(selectinload(CommunityPost.author)))
    post = result.scalar_one_or_none()
    if post is None or post.status != "APPROVED":
        raise HTTPException(status_code=404, detail="Approved community post not found")
    post.status = "TAKEN_DOWN"
    post.reviewed_by, post.reviewed_at = admin.id, utc_now()
    post.rejection_reason = payload.rejection_reason.strip()
    db.add(CommunityPostReview(post_id=post.id, admin_id=admin.id, action="TAKEN_DOWN", reason=post.rejection_reason))
    await db.commit()
    return _community_out(post)

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
