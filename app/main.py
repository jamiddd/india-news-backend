import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional, List
from fastapi import FastAPI, Depends, HTTPException, Query, BackgroundTasks, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy import desc, or_, func, text, tuple_
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.config import settings
from app.database import engine, Base, get_db
from app.models import Source, Article, StoryCluster, User, utc_now
from app.schemas import (
    SourceOut, StoryClusterOut, ArticleOut, PaginatedClustersOut,
    UserAuthRequest, UserAuthResponse, UserPreferences, AccountDeleteRequest,
)
from uuid import uuid4
from app.services.poller import poll_all_sources
from app.services.topic_filters import CONTENT_GATED_CATEGORIES, keyword_regex
from app.services.enrichment import enrich_cluster_with_ai
from app.services.firebase_auth import (
    verify_firebase_id_token,
    InvalidFirebaseIdToken,
    delete_firebase_user,
)

STATIC_DIR = Path(__file__).parent / "static"

# Arbitrary fixed key for a Postgres advisory lock guarding schema creation.
# Uvicorn runs 4 worker processes (see Dockerfile CMD), each independently
# executing this lifespan on startup — without a lock, they can race to
# CREATE TABLE for anything new, since create_all's "does it exist" check
# and the actual CREATE aren't atomic together. Observed live: deploying the
# community-post tables hit a UniqueViolation on Postgres's own pg_type
# catalog when two workers' CREATE TABLE calls landed at the same moment.
# Distinct from poller.py's POLL_LOCK_KEY (different purpose, same pattern).
SCHEMA_LOCK_KEY = 918273645

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.execute(text(f"SELECT pg_advisory_lock({SCHEMA_LOCK_KEY})"))
        try:
            await conn.run_sync(Base.metadata.create_all)
        finally:
            await conn.execute(text(f"SELECT pg_advisory_unlock({SCHEMA_LOCK_KEY})"))
    yield

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan
)

# Per-IP rate limiting, backed by the same Redis instance used elsewhere —
# a plain in-memory limiter would let each of the 4 uvicorn workers (see
# Dockerfile CMD) enforce its own separate count, effectively multiplying
# the real limit by ~4. default_limits is a blanket per-IP fallback for any
# route below without its own explicit @limiter.limit(...).
limiter = Limiter(key_func=get_remote_address, storage_uri=settings.REDIS_URL, default_limits=["100/minute"])
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

DEFAULT_PREFERENCES = UserPreferences(
    enabled_categories=["all", "national", "business", "official", "sports", "entertainment", "tech", "politics"]
)

@app.post(f"{settings.API_V1_STR}/auth/login", response_model=UserAuthResponse)
@limiter.limit("20/minute")
async def login_user(request: Request, payload: UserAuthRequest, db: AsyncSession = Depends(get_db)):
    """
    Create/update a user from a verified Firebase ID token and return the
    preferences saved for that account.

    `payload.uid` carries the Firebase ID token (a signed JWT, not a stable
    identifier) for both provider="email" and provider="google" — Firebase
    Authentication now backs both sign-in methods, so there's one verification
    path instead of a per-provider branch. The stable identity to key the user
    row on is the token's own "uid" claim (the same Firebase-assigned id
    regardless of which linked provider signed in), not the token string
    itself, which is different on every login. `payload.provider` is stored
    for informational/analytics value only — it no longer drives verification.
    """
    if not payload.uid:
        raise HTTPException(status_code=401, detail="Missing Firebase ID token")

    try:
        identity = await verify_firebase_id_token(payload.uid)
    except InvalidFirebaseIdToken as e:
        raise HTTPException(status_code=401, detail=f"Invalid Firebase ID token: {e}")

    if not identity.email_verified:
        raise HTTPException(status_code=403, detail="Email not verified")

    verified_email = identity.email
    verified_name = identity.name
    provider_uid = identity.uid

    result = await db.execute(select(User).where(User.provider_uid == provider_uid))
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
@limiter.limit("30/minute")
async def update_user_preferences(
    request: Request,
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
@limiter.exempt
async def root(request: Request):
    return {
        "status": "healthy",
        "app": settings.PROJECT_NAME,
        "version": settings.VERSION
    }

@app.get("/privacy", response_class=HTMLResponse)
@limiter.limit("60/minute")
async def privacy_policy(request: Request):
    return (STATIC_DIR / "privacy.html").read_text()

@app.get("/terms", response_class=HTMLResponse)
@limiter.limit("60/minute")
async def terms_of_service(request: Request):
    return (STATIC_DIR / "terms.html").read_text()

@app.post(f"{settings.API_V1_STR}/account/delete", status_code=204)
@limiter.limit("5/hour")
async def delete_account(request: Request, payload: AccountDeleteRequest, db: AsyncSession = Depends(get_db)):
    """
    Permanently deletes the caller's account. `payload.uid` carries a fresh
    Firebase ID token, verified server-side here — same as /auth/login. The
    account deleted is derived from the verified token's own uid claim, not
    a client-supplied user_id, so this endpoint can't be used to delete
    someone else's account by guessing/observing their internal id (unlike
    other endpoints, e.g. preferences update, which still trust a raw
    client-supplied user_id — see india-news-app-handoff.md).
    """
    try:
        identity = await verify_firebase_id_token(payload.uid)
    except InvalidFirebaseIdToken as e:
        raise HTTPException(status_code=401, detail=f"Invalid Firebase ID token: {e}")

    result = await db.execute(select(User).where(User.provider_uid == identity.uid))
    user = result.scalar_one_or_none()
    if user is not None:
        await db.delete(user)
        await db.commit()

    await delete_firebase_user(identity.uid)

@app.get(f"{settings.API_V1_STR}/sources", response_model=List[SourceOut])
@limiter.limit("30/minute")
async def list_sources(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Source))
    return result.scalars().all()

@app.post(f"{settings.API_V1_STR}/ingest/poll")
@limiter.limit("5/hour")
async def trigger_poll(request: Request, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    background_tasks.add_task(poll_all_sources, db)
    return {"message": "Ingestion polling triggered in background."}

@app.post(f"{settings.API_V1_STR}/clusters/{{cluster_id}}/enrich")
@limiter.limit("10/hour")
async def enrich_cluster_endpoint(request: Request, cluster_id: int, db: AsyncSession = Depends(get_db)):
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
@limiter.limit("60/minute")
async def search_story_clusters(
    request: Request,
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
            ai_enriched=cluster.ai_enriched,
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
@limiter.limit("60/minute")
async def list_story_clusters(
    request: Request,
    category: Optional[str] = Query(None, description="Category filter (national, business, official, northeast)"),
    limit: int = Query(20, ge=1, le=50),
    cursor: Optional[str] = Query(None, description="Cursor for pagination"),
    db: AsyncSession = Depends(get_db)
):
    is_all = not category or category.lower() == "all"

    query = select(StoryCluster).options(
        selectinload(StoryCluster.articles).selectinload(Article.source)
    )

    if is_all:
        # Default "All Stories" feed: ranked by importance (headline_score —
        # distinct-outlet corroboration decayed by recency, recomputed once
        # per poll cycle in poller.py), not raw recency. This is what keeps
        # a story 6 outlets are covering above a single regional outlet's
        # story that merely updated more recently.
        query = query.order_by(desc(StoryCluster.headline_score), desc(StoryCluster.id))
    else:
        cat = category.lower()
        subquery = (
            select(Article.cluster_id)
            .join(Source)
            .where(Source.category == cat)
        )
        # For tabs where the source's RSS section alone is too coarse a
        # signal (broad section feeds mixing in off-topic stories, or a
        # regional outlet occasionally running an unrelated wire story),
        # also require the article to actually mention something on-topic.
        # See app/services/topic_filters.py for why and the keyword lists.
        gate_keywords = CONTENT_GATED_CATEGORIES.get(cat)
        if gate_keywords:
            pattern = keyword_regex(gate_keywords)
            subquery = subquery.where(
                or_(
                    Article.title.op("~*")(pattern),
                    Article.snippet.op("~*")(pattern),
                )
            )
        query = query.where(StoryCluster.id.in_(subquery))
        # Category/region tabs stay pure reverse-chronological — someone who
        # picked a specific lens wants everything in it, in order, not a
        # curated subset.
        query = query.order_by(desc(StoryCluster.last_updated_at), desc(StoryCluster.id))

    if cursor:
        if is_all:
            # Compound "score:id" cursor — headline_score isn't monotonic
            # with id, so a plain id cursor can't express "everything after
            # this point in score order". Malformed/stale cursors (e.g. the
            # old bare-int format, from a client mid-scroll across a
            # deploy) are treated as "start over" rather than a 500.
            try:
                score_str, id_str = cursor.split(":", 1)
                query = query.where(
                    tuple_(StoryCluster.headline_score, StoryCluster.id) < (float(score_str), int(id_str))
                )
            except (ValueError, TypeError):
                pass
        else:
            try:
                query = query.where(StoryCluster.id < int(cursor))
            except (ValueError, TypeError):
                pass

    query = query.limit(limit + 1)
    result = await db.execute(query)
    clusters = result.scalars().all()

    has_more = len(clusters) > limit
    items = clusters[:limit]
    if has_more and items:
        last = items[-1]
        next_cursor = f"{last.headline_score}:{last.id}" if is_all else str(last.id)
    else:
        next_cursor = None

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
                ai_enriched=cluster.ai_enriched,
                articles=articles_out
            )
        )

    return PaginatedClustersOut(
        items=formatted_clusters,
        next_cursor=next_cursor,
        has_more=has_more
    )

@app.get(f"{settings.API_V1_STR}/clusters/{{cluster_id}}", response_model=StoryClusterOut)
@limiter.limit("60/minute")
async def get_story_cluster(request: Request, cluster_id: int, db: AsyncSession = Depends(get_db)):
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
        ai_enriched=cluster.ai_enriched,
        articles=articles_out
    )
