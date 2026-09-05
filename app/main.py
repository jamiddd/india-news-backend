import asyncio
import hashlib
import json
import logging
import random
from functools import lru_cache
from datetime import date, datetime, time, timedelta
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional, List
from fastapi import FastAPI, Depends, HTTPException, Query, BackgroundTasks, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy import desc, or_, func, text, tuple_, case
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.config import settings

logger = logging.getLogger(__name__)

# Error tracking — no-op entirely unless SENTRY_DSN is configured (local
# dev / CI without it behaves exactly as before this was added). Kept as a
# plain conditional import+init here rather than a separate module since
# it's genuinely this small and has no other call sites — sentry_sdk's
# FastAPI integration patches the framework globally at init time, it
# isn't something route handlers call into directly.
if settings.SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration

    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        integrations=[FastApiIntegration()],
        # Full error capture, low-volume perf sampling — this app's traffic
        # doesn't need APM-grade tracing, just "tell me when something
        # breaks in production" (the actual gap being closed here).
        traces_sample_rate=0.1,
    )
    logger.info("Sentry error tracking enabled.")
else:
    logger.info("SENTRY_DSN not set — error tracking disabled.")

from app.database import engine, Base, get_db
from app.redis_client import get_redis_client
from app.admin_session import session_csrf
from app.models import Source, Article, StoryCluster, User, DeviceToken, DailyCrossword, DailyPoll, PollOption, PollVote, GameSession, ReadEvent, SavedStory, UserSourceFollow, UserSourceBlock, StoryReport, Donation, utc_now
from app.schemas import (
    SourceOut, StoryClusterOut, ArticleOut, StoryClusterListOut, ArticleListOut,
    PaginatedClustersOut, PaginatedClustersListOut, ClustersCacheEnvelope, RelatedClustersOut,
    UserAuthRequest, UserAuthResponse, UserPreferences, AccountDeleteRequest,
    DeviceTokenRegisterRequest,
    DailyCrosswordOut, CrosswordCheckRequest, CrosswordCheckResponse,
    CrosswordRevealRequest, CrosswordRevealResponse,
    DailySudokuOut,
    DailyWordSearchOut,
    DailySpellingBeeOut, DailyWordLadderOut, DailyQuizOut, DailyWordleOut,
    WordOfTheDayOut, QuoteOfTheDayOut, OnThisDayOut, DailyHoroscopeOut, DailyPollOut, PollVoteRequest,
    GameSessionRequest, GameStatsOut, GameTypeStatsOut, VALID_GAME_TYPES,
    ReadEventRequest,
    DonationLinkRequest, DonationLinkResponse,
    SaveStoryRequest, SavedStoryOut, SavedStoriesOut,
    StarredSourcesOut,
    BlockedSourcesOut,
    ReportStoryRequest,
)
from app.services.affinity import record_engagement, score_clusters_for_user
from app.services.explore_bandit import pick_candidate, record_exposure, EXPLORE_PROMOTED_BOOST, EXPLORE_SLOT_POSITION
from uuid import uuid4
from app.services.poller import poll_all_sources
from app.services.topic_filters import CONTENT_GATED_CATEGORIES, keyword_regex
from app.services.enrichment import enrich_cluster_with_ai
from app.services.feed_gate import (
    LISTING_MAX_AGE,
    apply_feed_gate,
    gate_cache_marker,
    listing_age_anchor,
)
from app.services.related_stories import find_related_clusters
from app.services.donations import signature_matches, parse_captured_payment, create_payment_link, MalformedWebhook
from scripts.enrich_all_clusters import enrich_clusters

# Per-run ceiling for the recurring news-enrich.timer. At a 20-minute cadence
# this is ~7,200 clusters/day of headroom against ~1,100 new clusters/day, so
# it never throttles normal operation — it exists purely so a large backlog
# can't be drained (and paid for) in a single unattended tick.
TIMER_ENRICH_LIMIT = 100
from scripts.send_notifications import main as run_send_notifications
from app.services.crossword import get_or_create_puzzle, india_today
from app.services.sudoku import get_or_create_sudoku
from app.services.word_search import get_or_create_word_search
from app.services.daily_games import get_or_create_daily_games, fallback_quiz_questions, WORDLE_MAX_GUESSES
from app.services import wordlists
from app.services.editorial_features import get_or_create_editorial
from app.services.horoscope import get_or_create_horoscope
from app.services.polls import IST, activate_poll, serialize_poll, voter_hash
from sqlalchemy.dialects.postgresql import insert as pg_insert
from app.services.firebase_auth import (
    verify_firebase_id_token,
    InvalidFirebaseIdToken,
    delete_firebase_user,
)
from app.poll_admin import router as poll_admin_router
from app.quiz_admin import router as quiz_admin_router
from app.admin_home import router as admin_home_router
from app.story_reports_admin import router as story_reports_admin_router

STATIC_DIR = Path(__file__).parent / "static"


@lru_cache(maxsize=None)
def static_page(name: str) -> str:
    """Reads one of the static HTML pages, once per process.

    These files are baked into the image and cannot change while the container
    is running, so re-reading them from disk on every request buys nothing. It
    matters most for "/": the load balancer health-checks it every 10 seconds,
    which was 8,640 pointless disk reads a day to return a page that never
    changes. A deploy replaces the container, so the cache cannot go stale.
    """
    return (STATIC_DIR / name).read_text()


# Short-TTL read cache for the hot list endpoints (/clusters, /search).
# Redis was already provisioned for rate limiting (see `limiter` below) but
# sat otherwise idle for reads — every request hit Postgres directly even
# though the underlying data only actually changes once per poll cycle
# (~15 min). 5 minutes still surfaces a poll-triggered update promptly
# while collapsing many more reloads/concurrent clients requesting the same
# (category, cursor) page into one DB query than the old 30s did. For
# /clusters specifically, order rotation on reload comes from the
# post-cache weighted shuffle (see _weighted_shuffle's call site below),
# not from expiring this cache, so a long TTL doesn't make repeat loads
# look static. Deliberately fails open on any Redis error (network blip,
# Redis restart) — caching is a performance optimization, not a
# correctness dependency, so a cache miss/error just means "hit the DB
# like before", never a request failure.
CACHE_TTL_SECONDS = 300

async def _cache_get(key: str) -> Optional[str]:
    try:
        return await get_redis_client().get(key)
    except Exception:
        return None

async def _cache_set(key: str, value: str, ttl: int = CACHE_TTL_SECONDS):
    try:
        await get_redis_client().setex(key, ttl, value)
    except Exception:
        pass

def _parse_source_weights(raw: Optional[str]) -> dict:
    """Parses 'id:weight,id:weight' (see UserPreferences.source_weights /
    GET /clusters's source_weights param) into {source_id: weight}. Silently
    drops malformed pairs and clamps weight to [0.1, 5.0] rather than
    rejecting the whole request — a bad/stale entry from a client shouldn't
    500 the entire feed, and the clamp keeps a typo'd value (e.g. "0" or
    "999") from zeroing out or drowning out everything else in the feed."""
    weights: dict = {}
    if not raw:
        return weights
    for pair in raw.split(","):
        if ":" not in pair:
            continue
        sid_str, weight_str = pair.split(":", 1)
        try:
            sid = int(sid_str)
            weight = max(0.1, min(5.0, float(weight_str)))
        except ValueError:
            continue
        weights[sid] = weight
    return weights

# See _truncate_content_preview / ArticleListOut.content.
CONTENT_PREVIEW_CHAR_LIMIT = 600

# Exponent applied to each cluster's effective_score before it feeds the
# weighted shuffle below — 1.0 would stay closest to the real ranking (only
# near-exact ties swap), while lower values compress the effective spread so
# stories with a moderate (not just tiny) score gap can still shuffle
# together. 0.5 was chosen as a deliberately "noticeable" rotation, not a
# subtle one — retune this single constant if it needs to feel stronger or
# weaker once it's live.
FEED_SHUFFLE_STRENGTH = 0.5

def _weighted_shuffle(items: list, weight_fn, seed: str, strength: float = FEED_SHUFFLE_STRENGTH) -> list:
    """Relevance-weighted random permutation (Efraimidis-Spirakis key) so a
    reload can rotate the feed without ever hiding a genuinely dominant
    story behind a much weaker one. A cluster with a much higher weight than
    its neighbors still lands near the top almost every time (its key
    concentrates near 1); near-equal weights produce a near-uniform random
    order among themselves. Deterministic for a given (items, seed) pair —
    same seed always reproduces the same order — but a fresh seed per
    request is what actually makes the feed feel alive on reload. Does not
    mutate `items`; used only to decide display order, never to decide which
    clusters are fetched (see call site: applied after pagination/cursor
    logic has already picked the fixed set of clusters for this page)."""
    rng = random.Random(seed)
    def key(item):
        w = max(weight_fn(item), 1e-6) ** strength
        u = rng.random()
        return u ** (1.0 / w)
    return sorted(items, key=key, reverse=True)


def _cluster_to_out(cluster: StoryCluster) -> StoryClusterOut:
    """Builds a full StoryClusterOut (incl. article content/entities/topics/
    framing_comparison) from an ORM StoryCluster, filling
    ArticleOut.source_name (not a plain column — comes from art.source.name)
    by hand. NOT a plain StoryClusterOut.model_validate(cluster): that fails
    on the missing source_name field. Requires cluster.articles' .source to
    already be loaded (selectinload), same as every existing call site.
    Use this for single-cluster/detail responses; use _cluster_to_list_out
    for list endpoints (GET /clusters, /search) — see StoryClusterListOut."""
    articles_out = [
        ArticleOut(
            id=art.id,
            source_id=art.source_id,
            source_name=art.source.name if art.source else "Unknown",
            url=art.url,
            title=art.title,
            snippet=art.snippet,
            content=art.content,
            published_at=art.published_at,
            image_url=art.image_url,
            video_url=art.video_url,
            video_is_short=art.video_is_short,
            video_duration_seconds=art.video_duration_seconds,
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
        articles=articles_out,
    )


def _truncate_content_preview(content: Optional[str], limit: int = CONTENT_PREVIEW_CHAR_LIMIT) -> Optional[str]:
    """Word-boundary-safe clip of the scraped article body for list-endpoint
    responses — see ArticleListOut.content. `limit` is set well above the
    Android card's own ~420-char display budget (FeedNewsItemProduction.kt's
    CARD_SNIPPET_CHAR_BUDGET) so client-side wrapping/justification always
    has real text to work with, while still avoiding shipping the full
    3-8KB body to every feed load."""
    if not content:
        return content
    if len(content) <= limit:
        return content
    return content[:limit].rsplit(" ", 1)[0].rstrip() + "…"


def _cluster_to_list_out(cluster: StoryCluster) -> StoryClusterListOut:
    """Slim counterpart to _cluster_to_out for list endpoints — see
    StoryClusterListOut/ArticleListOut for what's dropped and why. Same
    selectinload requirement as _cluster_to_out."""
    articles_out = [
        ArticleListOut(
            id=art.id,
            source_id=art.source_id,
            source_name=art.source.name if art.source else "Unknown",
            url=art.url,
            title=art.title,
            snippet=art.snippet,
            content=_truncate_content_preview(art.content),
            published_at=art.published_at,
            image_url=art.image_url,
            video_url=art.video_url,
            video_is_short=art.video_is_short,
            video_duration_seconds=art.video_duration_seconds,
        )
        for art in cluster.articles
    ]
    return StoryClusterListOut(
        id=cluster.id,
        headline=cluster.headline,
        summary=cluster.summary,
        article_count=cluster.article_count,
        first_seen_at=cluster.first_seen_at,
        last_updated_at=cluster.last_updated_at,
        articles=articles_out,
    )

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
            # create_all only adds missing *tables*, not columns on tables
            # that already exist — daily_editorial_features predates
            # background_image, so add it here idempotently on every startup.
            await conn.execute(text("ALTER TABLE daily_editorial_features ADD COLUMN IF NOT EXISTS background_image JSON"))
        finally:
            await conn.execute(text(f"SELECT pg_advisory_unlock({SCHEMA_LOCK_KEY})"))
    yield

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan
)
app.include_router(poll_admin_router)
app.include_router(quiz_admin_router)
app.include_router(admin_home_router)
app.include_router(story_reports_admin_router)

# Static assets for the landing page (device screenshots). Mounted rather
# than inlined as data: URIs because the pages are served through the
# lru_cache'd static_page() helper — base64-ing a dozen PNGs into that
# string would hold megabytes resident per worker and re-send them on every
# uncached page view.
#
# CachedStaticFiles rather than plain StaticFiles because of the rate
# limiter configured below: default_limits is 100/minute per IP, and a
# single landing-page view pulls one request per image. Immutable
# year-long caching means a returning visitor spends zero requests against
# that budget, and the filenames are versioned by hand when a shot is
# re-taken.
class CachedStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


app.mount("/static", CachedStaticFiles(directory=str(STATIC_DIR)), name="static")

# Every JSON response — cluster lists especially, with full article bodies
# and JSON columns — was going out uncompressed end to end (confirmed: no
# `encode` in the live Caddy config either). OkHttp on the client already
# sends `Accept-Encoding: gzip` and transparently inflates, so this is a
# same-day ~5-10x cut in client-facing bytes with zero client change.
# 500-byte floor so tiny responses (e.g. {"message": "..."}) skip the
# compression overhead entirely.
app.add_middleware(GZipMiddleware, minimum_size=500)

# Per-IP rate limiting, backed by the same Redis instance used elsewhere —
# a plain in-memory limiter would let each of the 4 uvicorn workers (see
# Dockerfile CMD) enforce its own separate count, effectively multiplying
# the real limit by ~4. default_limits is a blanket per-IP fallback for any
# route below without its own explicit @limiter.limit(...).
limiter = Limiter(key_func=get_remote_address, storage_uri=settings.REDIS_URL, default_limits=["100/minute"])
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

@app.middleware("http")
async def enforce_min_client_version(request: Request, call_next):
    """
    API version negotiation: the client sends its own versionCode as
    X-Client-Version (see NewsApiClient.kt). If it's present and below
    settings.MIN_SUPPORTED_APP_VERSION_CODE, reject with 426 Upgrade
    Required and a clear message, rather than letting an old client hit a
    response shape it can't parse or a removed field it depends on.

    Fails open by design: no header at all (every client shipped before
    this existed, or a non-app caller like curl/the OpenAPI docs UI) is
    allowed through unchanged — this only blocks a client that identifies
    itself AND is confirmed too old, never an unknown one.
    """
    client_version = request.headers.get("X-Client-Version")
    if client_version is not None:
        try:
            if int(client_version) < settings.MIN_SUPPORTED_APP_VERSION_CODE:
                return JSONResponse(
                    status_code=426,
                    content={"detail": "Please update the app to continue — this version is no longer supported."},
                )
        except ValueError:
            pass  # malformed header — ignore rather than block on it
    return await call_next(request)

@app.middleware("http")
async def add_etag_and_revalidate(request: Request, call_next):
    """
    Conditional-GET support: hashes the (uncompressed — this runs inside
    GZipMiddleware in the stack, added above, so it sees the body before
    compression) response body as its ETag, and short-circuits to a bare
    304 if the client's If-None-Match already matches. The 300s Redis
    cache (see _cache_get/_cache_set) already collapses repeat requests on
    the *backend* side within that window; this collapses the *client*
    transfer too — a tab re-visit or a pull-to-refresh right after a
    previous one gets an empty 304 instead of re-downloading the same
    gzipped JSON. Cache-Control is deliberately max-age=0/must-revalidate
    — every request still round-trips to the server (so this can never
    itself serve stale data), only the body transfer is skippable. GET
    only, and only on a clean 200 — errors, redirects, and non-GET
    (POST/PUT/DELETE mutations) pass through untouched.
    """
    if request.method != "GET":
        return await call_next(request)

    response = await call_next(request)
    if response.status_code != 200:
        return response

    body = b"".join([chunk async for chunk in response.body_iterator])
    etag = hashlib.md5(body).hexdigest()

    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={
            "ETag": etag,
            "Cache-Control": "private, max-age=0, must-revalidate",
        })

    new_headers = dict(response.headers)
    new_headers["ETag"] = etag
    new_headers["Cache-Control"] = "private, max-age=0, must-revalidate"
    # Content-Length belonged to whatever body shape call_next produced —
    # recomputed for the exact bytes being sent now rather than trusted,
    # since some response paths (e.g. StreamingResponse) never set it.
    new_headers["Content-Length"] = str(len(body))
    return Response(
        content=body,
        status_code=response.status_code,
        headers=new_headers,
        media_type=response.media_type,
    )

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


@app.post(f"{settings.API_V1_STR}/users/{{user_id}}/device-tokens", status_code=200)
@limiter.limit("30/minute")
async def register_device_token(
    request: Request,
    user_id: str,
    payload: DeviceTokenRegisterRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Upsert an FCM registration token for push notifications. Keyed unique on
    fcm_token alone (not user_id+token): if this exact token already belongs
    to a *different* user_id, reassign it rather than erroring or duplicating
    — covers a shared/reused device where a different account just logged
    in, which would otherwise leave the token still pointing at the previous
    owner. If it already belongs to this user, just touch updated_at.
    """
    result = await db.execute(select(User).where(User.id == user_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="User not found")

    existing = await db.execute(select(DeviceToken).where(DeviceToken.fcm_token == payload.fcm_token))
    token_row = existing.scalar_one_or_none()
    if token_row is None:
        db.add(DeviceToken(user_id=user_id, fcm_token=payload.fcm_token, platform=payload.platform))
    else:
        token_row.user_id = user_id
        token_row.platform = payload.platform
        token_row.updated_at = utc_now()

    await db.commit()
    return {"message": "Device token registered"}


@app.get("/", response_class=HTMLResponse)
@limiter.exempt
async def home(request: Request):
    """The public landing page.

    Was a JSON health blob, which meant anyone visiting openindiannews.com —
    including Razorpay's activation reviewer, who is required to see what the
    business sells and at what price — got `{"status": "healthy"}` and no
    evidence a product existed. The JSON moved to /status; /health (below) was
    already the real health check and is unchanged.
    """
    return static_page("home.html")

@app.get("/status")
@limiter.exempt
async def status(request: Request):
    """The former `/` payload, kept for anything that was pointed at it."""
    return {
        "status": "healthy",
        "app": settings.PROJECT_NAME,
        "version": settings.VERSION
    }

@app.get("/health")
@limiter.exempt
async def health_check(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Real dependency check for uptime monitoring, distinct from "/" — "/"
    only proves the FastAPI process is up and answering HTTP, which stays
    green even if Postgres or Redis are unreachable (every DB-touching route
    would still 500). This actually pings both, so an external monitor
    (UptimeRobot etc.) pointed here can distinguish "app is fine" from
    "app is up but its dependencies are down" — see
    docs/production-readiness-gaps.md gap #6.
    """
    checks = {}

    try:
        await db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e}"

    try:
        await get_redis_client().ping()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {e}"

    healthy = all(v == "ok" for v in checks.values())
    status_code = 200 if healthy else 503
    return JSONResponse(
        status_code=status_code,
        content={"status": "healthy" if healthy else "unhealthy", "checks": checks},
    )


# Games keep a rolling 30-day archive (today + the 29 days before it) that a
# client can request by passing ?date=YYYY-MM-DD on any /daily route below.
# Puzzles are never deleted (see app/models.py's Daily* tables), so this is
# purely a request-time policy limiting how far back a client can ask —
# not a storage limit. Bump GAME_HISTORY_WINDOW_DAYS to widen it later
# without touching stored data.
GAME_HISTORY_WINDOW_DAYS = 30


def resolve_puzzle_date(date_str: str | None) -> date:
    if date_str is None:
        return india_today()
    try:
        requested = date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="date must be in YYYY-MM-DD format")
    today = india_today()
    earliest = today - timedelta(days=GAME_HISTORY_WINDOW_DAYS - 1)
    if requested > today:
        raise HTTPException(status_code=400, detail="date cannot be in the future")
    if requested < earliest:
        raise HTTPException(status_code=400, detail=f"date must be within the last {GAME_HISTORY_WINDOW_DAYS} days")
    return requested


@app.get(f"{settings.API_V1_STR}/crossword/daily", response_model=DailyCrosswordOut)
@limiter.limit("30/minute")
async def daily_crossword(request: Request, date: str | None = Query(None), db: AsyncSession = Depends(get_db)):
    puzzle = await get_or_create_puzzle(db, resolve_puzzle_date(date))
    return {
        "date": puzzle.puzzle_date,
        "size": puzzle.size,
        "rows": puzzle.grid,
        "clues": puzzle.clues,
    }


@app.get(f"{settings.API_V1_STR}/sudoku/daily", response_model=DailySudokuOut)
@limiter.limit("30/minute")
async def daily_sudoku(request: Request, date: str | None = Query(None), db: AsyncSession = Depends(get_db)):
    sudoku = await get_or_create_sudoku(db, resolve_puzzle_date(date))
    return {
        "date": sudoku.puzzle_date,
        "puzzle": sudoku.puzzle,
        "solution": sudoku.solution,
    }


@app.get(f"{settings.API_V1_STR}/word-search/daily", response_model=DailyWordSearchOut)
@limiter.limit("30/minute")
async def daily_word_search(request: Request, date: str | None = Query(None), db: AsyncSession = Depends(get_db)):
    puzzle = await get_or_create_word_search(db, resolve_puzzle_date(date))
    return {
        "date": puzzle.puzzle_date,
        "theme": puzzle.theme,
        "size": len(puzzle.grid),
        "rows": puzzle.grid,
        "words": puzzle.words,
    }


@app.get(f"{settings.API_V1_STR}/spelling-bee/daily", response_model=DailySpellingBeeOut)
@limiter.limit("30/minute")
async def daily_spelling_bee(request: Request, date: str | None = Query(None), db: AsyncSession = Depends(get_db)):
    bee, _, _, _ = await get_or_create_daily_games(db, resolve_puzzle_date(date))
    return {"date": bee.puzzle_date, "letters": bee.letters, "center_letter": bee.center_letter, "words": bee.words}


@app.get(f"{settings.API_V1_STR}/word-ladder/daily", response_model=DailyWordLadderOut)
@limiter.limit("30/minute")
async def daily_word_ladder(request: Request, date: str | None = Query(None), db: AsyncSession = Depends(get_db)):
    _, ladder, _, _ = await get_or_create_daily_games(db, resolve_puzzle_date(date))
    return {
        "date": ladder.puzzle_date,
        "start_word": ladder.start_word,
        "target_word": ladder.target_word,
        "allowed_words": ladder.allowed_words,
        "optimal_steps": ladder.optimal_steps,
    }


@app.get(f"{settings.API_V1_STR}/wordle/daily", response_model=DailyWordleOut)
@limiter.limit("30/minute")
async def daily_wordle(request: Request, date: str | None = Query(None), db: AsyncSession = Depends(get_db)):
    _, _, _, wordle = await get_or_create_daily_games(db, resolve_puzzle_date(date))
    # Recomputed per request rather than stored per puzzle -- the same ~6,400
    # words every day (see DailyWordle.answer's note). The answer is folded in
    # so it is always typeable even on the "curated" fallback path, where it
    # comes from WORDLE_FALLBACKS instead of the word list.
    accepted = sorted(set(wordlists.accepted_guesses()) | {wordle.answer})
    return {
        "date": wordle.puzzle_date,
        "answer": wordle.answer,
        "word_length": len(wordle.answer),
        "max_guesses": WORDLE_MAX_GUESSES,
        "accepted_guesses": accepted,
    }


@app.get(f"{settings.API_V1_STR}/quiz/daily", response_model=DailyQuizOut)
@limiter.limit("30/minute")
async def daily_quiz(request: Request, date: str | None = Query(None), db: AsyncSession = Depends(get_db)):
    puzzle_date = resolve_puzzle_date(date)
    _, _, quiz, _ = await get_or_create_daily_games(db, puzzle_date)
    # An AI draft is not publishable content until a human has approved it, so
    # an unapproved (or rejected) quiz serves the reviewed curated set instead
    # of leaking the draft. See app/quiz_admin.py.
    if quiz.status != "approved":
        return {"date": puzzle_date, "questions": fallback_quiz_questions(puzzle_date)}
    return {"date": quiz.puzzle_date, "questions": quiz.questions}


@app.get(f"{settings.API_V1_STR}/word-of-the-day", response_model=WordOfTheDayOut)
@limiter.limit("30/minute")
async def word_of_the_day(request: Request, date: str | None = Query(None), db: AsyncSession = Depends(get_db)):
    feature = await get_or_create_editorial(db, resolve_puzzle_date(date))
    return {"date": feature.feature_date, **feature.word}


@app.get(f"{settings.API_V1_STR}/quote-of-the-day", response_model=QuoteOfTheDayOut)
@limiter.limit("30/minute")
async def quote_of_the_day(request: Request, date: str | None = Query(None), db: AsyncSession = Depends(get_db)):
    feature = await get_or_create_editorial(db, resolve_puzzle_date(date))
    return {"date": feature.feature_date, **feature.quote, "background_image": feature.background_image}


@app.get(f"{settings.API_V1_STR}/on-this-day", response_model=OnThisDayOut)
@limiter.limit("30/minute")
async def on_this_day(request: Request, date: str | None = Query(None), db: AsyncSession = Depends(get_db)):
    feature = await get_or_create_editorial(db, resolve_puzzle_date(date))
    return {"date": feature.feature_date, "events": feature.historical_events, "attribution": "Wikipedia contributors (CC BY-SA)"}


@app.get(f"{settings.API_V1_STR}/horoscope/daily/{{sign}}", response_model=DailyHoroscopeOut)
@limiter.limit("30/minute")
async def daily_horoscope(request: Request, sign: str, db: AsyncSession = Depends(get_db)):
    forecast = await get_or_create_horoscope(db, india_today(), sign)
    return {"date": forecast.forecast_date, **forecast.forecast}


async def _current_poll(db: AsyncSession) -> DailyPoll:
    now = datetime.now(IST)
    poll_day = now.date() if now.time() >= time(9) else now.date() - timedelta(days=1)
    poll = await db.scalar(select(DailyPoll).where(DailyPoll.poll_date == poll_day, DailyPoll.status == "active"))
    if poll is None and now.time() >= time(9):
        poll = await activate_poll(db, poll_day)
    if poll is None:
        raise HTTPException(status_code=404, detail="Today's poll is not available yet")
    return poll


@app.get(f"{settings.API_V1_STR}/polls/daily", response_model=DailyPollOut)
@limiter.limit("60/minute")
async def daily_poll(request: Request, db: AsyncSession = Depends(get_db)):
    poll = await _current_poll(db)
    installation_id = request.headers.get("X-Installation-ID")
    hashed = voter_hash(installation_id) if installation_id else None
    return await serialize_poll(db, poll, hashed)


@app.put(f"{settings.API_V1_STR}/polls/daily/vote", response_model=DailyPollOut)
@limiter.limit("20/minute")
async def vote_daily_poll(request: Request, payload: PollVoteRequest, db: AsyncSession = Depends(get_db)):
    installation_id = request.headers.get("X-Installation-ID")
    if not installation_id:
        raise HTTPException(status_code=422, detail="Missing installation identifier")
    hashed = voter_hash(installation_id)
    poll = await _current_poll(db)
    option = await db.scalar(select(PollOption).where(PollOption.id == payload.option_id, PollOption.poll_id == poll.id))
    if option is None:
        raise HTTPException(status_code=422, detail="Option does not belong to today's poll")
    statement = pg_insert(PollVote).values(poll_id=poll.id, option_id=option.id, voter_hash=hashed).on_conflict_do_update(
        constraint="uq_poll_vote_voter", set_={"option_id": option.id, "updated_at": utc_now()}
    )
    await db.execute(statement); await db.commit()
    return await serialize_poll(db, poll, hashed)


@app.post(f"{settings.API_V1_STR}/crossword/daily/check", response_model=CrosswordCheckResponse)
@limiter.limit("30/minute")
async def check_daily_crossword(
    request: Request,
    payload: CrosswordCheckRequest,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(DailyCrossword).where(DailyCrossword.puzzle_date == payload.date))
    puzzle = result.scalar_one_or_none()
    if puzzle is None:
        raise HTTPException(status_code=404, detail="Puzzle not found")

    entered = {(cell.row, cell.col): cell.letter.upper() for cell in payload.cells}
    target: set[tuple[int, int]] = set()
    if payload.scope == "word":
        direction = (payload.clue_direction or "").lower()
        clue = next(
            (item for item in puzzle.clues if item["number"] == payload.clue_number and item["direction"] == direction),
            None,
        )
        if clue is None:
            raise HTTPException(status_code=400, detail="A valid clue is required for word checking")
        dr, dc = (0, 1) if direction == "across" else (1, 0)
        target = {(clue["row"] + i * dr, clue["col"] + i * dc) for i in range(clue["length"])}
    elif payload.scope == "grid":
        target = {
            (row, col)
            for row in range(puzzle.size)
            for col in range(puzzle.size)
            if puzzle.solution[row][col] != "#"
        }
    else:
        raise HTTPException(status_code=400, detail="scope must be 'word' or 'grid'")

    incorrect = [
        [row, col]
        for row, col in sorted(target)
        if (row, col) in entered and entered[(row, col)] != puzzle.solution[row][col]
    ]
    all_open = {
        (row, col)
        for row in range(puzzle.size)
        for col in range(puzzle.size)
        if puzzle.solution[row][col] != "#"
    }
    complete = all(entered.get(cell) == puzzle.solution[cell[0]][cell[1]] for cell in all_open)
    return {"incorrect_cells": incorrect, "complete": complete}


@app.post(f"{settings.API_V1_STR}/crossword/daily/reveal", response_model=CrosswordRevealResponse)
@limiter.limit("20/minute")
async def reveal_daily_crossword(
    request: Request,
    payload: CrosswordRevealRequest,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(DailyCrossword).where(DailyCrossword.puzzle_date == payload.date))
    puzzle = result.scalar_one_or_none()
    if puzzle is None:
        raise HTTPException(status_code=404, detail="Puzzle not found")
    letter = puzzle.solution[payload.row][payload.col]
    if letter == "#":
        raise HTTPException(status_code=400, detail="Blocked cells cannot be revealed")
    return {"row": payload.row, "col": payload.col, "letter": letter}

@app.get("/about", response_class=HTMLResponse)
@limiter.limit("60/minute")
async def about(request: Request):
    """About Us. One of the pages Razorpay's activation check looks for by URL:
    a section anchored on the landing page does not reliably satisfy it."""
    return static_page("about.html")


@app.get("/pricing", response_class=HTMLResponse)
@limiter.limit("60/minute")
async def pricing(request: Request):
    """Pricing details, likewise required as its own URL. States that the app
    is free and that a donation unlocks nothing -- the same wording as / and
    /refunds, which was written for the activation reviewer."""
    return static_page("pricing.html")


@app.get("/donate", response_class=HTMLResponse)
@limiter.limit("60/minute")
async def donate_page(request: Request):
    """The web donation flow.

    Posts to the same /donations/link endpoint the Android app uses, so the
    two paths mint links identically and cannot drift. Grants nothing on
    return, for the reason stated above the donation endpoints: the moment a
    donation unlocks app functionality it stops being a donation.
    """
    return static_page("donate.html")


@app.get("/download", response_class=HTMLResponse)
@limiter.limit("60/minute")
async def download_page(request: Request):
    """Install page for the Android build.

    Exists because the app is not on Google Play yet, and Razorpay's
    activation form wants an app link a reviewer can actually open. Play's
    pre-production tracks are no use for that: internal testing is an
    allowlist-only URL, closed testing is not publicly searchable, and open
    testing needs production access first. So the build is hosted directly
    and this page is the link that gets submitted.
    """
    return static_page("download.html")


@app.get("/download/apk")
@limiter.limit("30/minute")
async def download_apk(request: Request):
    """Redirects to the hosted build. A redirect rather than a file response
    so the APK never has to live in this repo or the image -- see
    APK_DOWNLOAD_URL in config.py."""
    if not settings.APK_DOWNLOAD_URL:
        raise HTTPException(
            status_code=404,
            detail="No build is currently published for direct download.",
        )
    return RedirectResponse(settings.APK_DOWNLOAD_URL, status_code=302)


@app.get("/privacy", response_class=HTMLResponse)
@limiter.limit("60/minute")
async def privacy_policy(request: Request):
    return static_page("privacy.html")

@app.get("/terms", response_class=HTMLResponse)
@limiter.limit("60/minute")
async def terms_of_service(request: Request):
    return static_page("terms.html")

# Both pages below are required for Razorpay merchant activation, which checks
# that the business website publishes a refund/cancellation policy and
# reachable contact details. They also cover the equivalent Play listing
# expectations.

@app.get("/refunds", response_class=HTMLResponse)
@limiter.limit("60/minute")
async def refund_policy(request: Request):
    return static_page("refunds.html")

@app.get("/contact", response_class=HTMLResponse)
@limiter.limit("60/minute")
async def contact_us(request: Request):
    return static_page("contact.html")

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

@app.post(f"{settings.API_V1_STR}/ingest/enrich")
@limiter.limit("5/hour")
async def trigger_enrich(
    request: Request,
    background_tasks: BackgroundTasks,
    since_days: float = Query(2.0, description="Enrich clusters touched in the last N days"),
    force_all: bool = Query(False, description="Re-enrich even already-ai_enriched clusters (use for one-off backfills, not the recurring timer)"),
    limit: Optional[int] = Query(TIMER_ENRICH_LIMIT, description="Max clusters per run. Explicitly pass 0 for unbounded (one-off backfills only)."),
):
    # enrich_clusters() opens its own DB session internally rather than
    # taking the request-scoped `db` (contrast trigger_poll above, which
    # passes its request-scoped session into the background task) — a
    # background task must not depend on a session FastAPI may already be
    # closing once the response is sent.
    # limit=0 is the explicit opt-out; anything else caps the run. Passing
    # None here (the old behaviour) meant UNBOUNDED, because enrich_clusters
    # treats a windowed run as "already bounded by the window" and drops its
    # own DEFAULT_BATCH_LIMIT. With ~9,500 unenriched clusters sitting inside
    # the default 2-day window, one timer tick would enrich all of them: on
    # 2026-09-02 that drained ~6,500 clusters and ~$20 of credit in a few
    # hours, almost entirely on singletons. A recurring timer must never be
    # able to spend unboundedly.
    effective_limit = None if limit == 0 else limit
    background_tasks.add_task(enrich_clusters, effective_limit, force_all, since_days)
    return {
        "message": (
            f"Enrichment triggered in background (since_days={since_days}, "
            f"force_all={force_all}, limit={effective_limit})."
        )
    }

@app.post(f"{settings.API_V1_STR}/ingest/notify")
@limiter.limit("5/hour")
async def trigger_notify(request: Request, background_tasks: BackgroundTasks):
    """Fires scripts/send_notifications.py's breaking+daily push run. Meant
    to be hit by a systemd timer (news-notify.timer) every ~15 min,
    mirroring /ingest/poll and /ingest/enrich above — see that script's
    module docstring for what "breaking" vs "daily" actually send.
    run_send_notifications() opens its own DB session internally (same
    reason as enrich_clusters above) and holds its own Postgres advisory
    lock, so an overlapping/late-running background task from a prior timer
    tick is a safe no-op rather than a double-send."""
    background_tasks.add_task(run_send_notifications)
    return {"message": "Notification run triggered in background."}

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

@app.get(f"{settings.API_V1_STR}/search", response_model=PaginatedClustersListOut)
@limiter.limit("60/minute")
async def search_story_clusters(
    request: Request,
    q: str = Query(..., min_length=2, description="Search query string across headlines and summaries"),
    limit: int = Query(20, ge=1, le=50),
    cursor: Optional[int] = Query(None, description="Cursor for pagination"),
    db: AsyncSession = Depends(get_db)
):
    gate = gate_cache_marker()
    cache_key = f"cache:search:{gate}:{q}:{limit}:{cursor or ''}"
    cached = await _cache_get(cache_key)
    if cached is not None:
        return Response(content=cached, media_type="application/json")

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
    query = apply_feed_gate(query)

    if cursor:
        query = query.where(StoryCluster.id < cursor)

    query = query.limit(limit + 1)
    result = await db.execute(query)
    clusters = result.scalars().all()

    has_more = len(clusters) > limit
    items = clusters[:limit]
    next_cursor = str(items[-1].id) if has_more and items else None

    formatted_clusters = [_cluster_to_list_out(cluster) for cluster in items]

    result_out = PaginatedClustersListOut(
        items=formatted_clusters,
        next_cursor=next_cursor,
        has_more=has_more
    )
    await _cache_set(cache_key, result_out.model_dump_json())
    return result_out

@app.get(f"{settings.API_V1_STR}/clusters", response_model=PaginatedClustersListOut)
@limiter.limit("60/minute")
async def list_story_clusters(
    request: Request,
    category: Optional[str] = Query(None, description="Category filter (national, business, official, northeast)"),
    limit: int = Query(20, ge=1, le=50),
    cursor: Optional[str] = Query(None, description="Cursor for pagination"),
    source_weights: Optional[str] = Query(
        None,
        description="Comma-separated source_id:weight pairs (e.g. '12:2.0,45:1.5') boosting the All Stories "
                     "ranking — a cluster's effective score is headline_score * the highest weight among its "
                     "contributing sources (1.0 if none match). Ignored outside the All Stories tab, which is "
                     "the only ranked (not chronological) feed. Stateless by design, same as `category` — the "
                     "client resends the user's UserPreferences.source_weights on every request rather than the "
                     "server looking them up, since /clusters has no per-request auth to know who's asking.",
    ),
    seed: Optional[str] = Query(
        None,
        max_length=64,
        description="Client-generated per-load token for the All Stories tab. When present on a fresh first "
                     "page (no cursor), this page's results are reordered via a relevance-weighted shuffle "
                     "(see _weighted_shuffle) so reloading feels fresh without changing which stories appear "
                     "or breaking pagination — headline_score still decides *which* clusters are on the page, "
                     "the seed only decides their display order. Ignored once `cursor` is set (mid-pagination) "
                     "and outside the All Stories tab. Generate a new value on every fresh load (app open, "
                     "pull-to-refresh); omit or reuse it during infinite scroll.",
    ),
    user_id: Optional[str] = Query(
        None,
        description="Feed ranking redesign, piece 3 (explore-slot bandit): when present on a "
                     "fresh All Stories first page (no cursor), a low-source-count candidate "
                     "story may be spliced into a fixed slot (see EXPLORE_SLOT_POSITION) and "
                     "logged as an exposure for this user, feeding the promotion decision in "
                     "app.services.explore_bandit. Optional and otherwise inert — /clusters "
                     "remains usable anonymously; omit to opt out of the experiment.",
    ),
    min_sources: Optional[int] = Query(
        None,
        ge=1,
        description="Only return clusters corroborated by at least this many distinct outlets "
                     "(StoryCluster.distinct_source_count). Applies to any category, not just "
                     "All Stories — e.g. the app's 'Top Headlines' tab passes 2 here so a "
                     "single-outlet story (regional wire pickup, local-only coverage) never "
                     "counts as a headline regardless of its headline_score.",
    ),
    source_id: Optional[int] = Query(
        None,
        description="Pin the feed to a single publisher (Reorder Topics 'add a source as a "
                     "topic' tab) — returns only clusters with at least one article from this "
                     "Source.id, reverse-chronological, ignoring `category`/`seed`/`user_id`/"
                     "`source_weights`/the All Stories ranking entirely. Mutually exclusive "
                     "with those in intent, not enforced; if set, it wins.",
    ),
    db: AsyncSession = Depends(get_db)
):
    # `seed`/`user_id` deliberately excluded from the cache key: neither
    # affects which clusters this query returns (see ClustersCacheEnvelope's
    # docstring in app/schemas.py) — only how the cached page gets
    # reordered/spliced per request below, which happens on every request,
    # cache hit or miss.
    # v3: ArticleListOut gained a truncated `content` preview field — bump
    # the key so caches from before that change don't serve stale null
    # content until their TTL naturally expires.
    gate = gate_cache_marker()
    cache_key = f"cache:clusters:v3:{gate}:{category or 'all'}:{limit}:{cursor or ''}:{source_weights or ''}:{min_sources or ''}:{source_id or ''}"
    cached = await _cache_get(cache_key)

    is_source_filter = source_id is not None
    is_all = not is_source_filter and (not category or category.lower() == "all")
    weights = _parse_source_weights(source_weights) if is_all else {}

    if cached is not None:
        envelope = ClustersCacheEnvelope.model_validate_json(cached)
        cluster_outs = envelope.items
        item_weights = envelope.weights
        next_cursor = envelope.next_cursor
        has_more = envelope.has_more
    else:
        # Feed ranking redesign, piece 3: a promoted explore candidate gets a
        # real, live multiplier here — not a shadow signal like piece 1's
        # entity_boost, this is the actual mechanism that rescues a buried
        # story for everyone once it's earned it. See app.services.explore_bandit.
        explore_boost_expr = case(
            (StoryCluster.explore_status == "promoted", EXPLORE_PROMOTED_BOOST), else_=1.0
        )

        query = select(StoryCluster).options(
            selectinload(StoryCluster.articles).selectinload(Article.source)
        ).where(listing_age_anchor() >= utc_now() - LISTING_MAX_AGE)
        query = apply_feed_gate(query)

        if min_sources:
            query = query.where(StoryCluster.distinct_source_count >= min_sources)

        if is_all:
            # Default "All Stories" feed: ranked by importance (headline_score —
            # distinct-outlet corroboration decayed by recency, recomputed once
            # per poll cycle in poller.py), not raw recency. This is what keeps
            # a story 6 outlets are covering above a single regional outlet's
            # story that merely updated more recently.
            if weights:
                # Per-cluster boost = the highest weight among its contributing
                # sources (a cluster with a boosted source among 5 others still
                # gets the boost — not diluted by the unboosted majority). Built
                # as a correlated subquery rather than joining Article directly
                # onto the main query, so it can't fan out StoryCluster rows
                # (one per matching article) the way a plain join would.
                weight_case = case(weights, value=Article.source_id, else_=1.0)
                boost_subq = (
                    select(Article.cluster_id.label("cluster_id"), func.max(weight_case).label("boost"))
                    .group_by(Article.cluster_id)
                    .subquery()
                )
                boost_expr = func.coalesce(boost_subq.c.boost, 1.0)
                effective_score = StoryCluster.headline_score * boost_expr * explore_boost_expr
                query = query.outerjoin(boost_subq, boost_subq.c.cluster_id == StoryCluster.id)
            else:
                effective_score = StoryCluster.headline_score * explore_boost_expr
            query = query.order_by(desc(effective_score), desc(StoryCluster.id))
        elif is_source_filter:
            subquery = select(Article.cluster_id).where(Article.source_id == source_id)
            query = query.where(StoryCluster.id.in_(subquery))
            query = query.order_by(desc(StoryCluster.last_updated_at), desc(StoryCluster.id))
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
                # Must compare against the same expression used in ORDER BY
                # (effective_score, boosted or not) — comparing against raw
                # headline_score while boosted would skip/duplicate rows once a
                # boosted story sorts out of its unboosted position.
                try:
                    score_str, id_str = cursor.split(":", 1)
                    query = query.where(
                        tuple_(effective_score, StoryCluster.id) < (float(score_str), int(id_str))
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
            if is_all:
                # Recomputed in Python from the same weights dict rather than
                # selected as an extra SQL column — `last` is a plain mapped
                # StoryCluster (from .scalars()), and its already-loaded
                # .articles (via selectinload) give us everything needed to
                # reproduce the identical boost the SQL query used to order it.
                last_boost = max((weights.get(a.source_id, 1.0) for a in last.articles), default=1.0)
                last_explore_boost = EXPLORE_PROMOTED_BOOST if last.explore_status == "promoted" else 1.0
                next_cursor = f"{last.headline_score * last_boost * last_explore_boost}:{last.id}"
            else:
                next_cursor = str(last.id)
        else:
            next_cursor = None

        # Same weight the old inline shuffle used (headline_score * source
        # boost * explore boost) — precomputed here and cached alongside the
        # formatted clusters so a future cache hit can reshuffle without the
        # ORM objects (headline_score etc. aren't on StoryClusterOut).
        cluster_outs = [_cluster_to_list_out(c) for c in items]
        item_weights = [
            c.headline_score
            * max((weights.get(a.source_id, 1.0) for a in c.articles), default=1.0)
            * (EXPLORE_PROMOTED_BOOST if c.explore_status == "promoted" else 1.0)
            for c in items
        ]

        envelope = ClustersCacheEnvelope(
            items=cluster_outs, weights=item_weights, next_cursor=next_cursor, has_more=has_more,
        )
        await _cache_set(cache_key, envelope.model_dump_json())

    # Reorder (never refilter/repaginate) a fresh All Stories first page for
    # display, so reloading rotates the feed instead of returning the exact
    # same order every time headline_score hasn't recomputed yet (it only
    # updates once per poll cycle — see poller.py). next_cursor above is
    # already locked in from the real deterministic order, so this can't
    # skip or duplicate clusters across pages — it only shuffles what's
    # already been decided will appear on *this* page. Runs on cache hits
    # too (using the cached `item_weights`) so a long cache TTL doesn't
    # make reloads look static.
    if is_all and seed and cursor is None and cluster_outs:
        shuffled = _weighted_shuffle(
            list(zip(cluster_outs, item_weights)),
            weight_fn=lambda pair: pair[1],
            seed=seed,
        )
        display_items = [pair[0] for pair in shuffled]
    else:
        display_items = cluster_outs

    # Feed ranking redesign, piece 3: explore-slot bandit. Only on a fresh,
    # logged-in All Stories first page — see EXPLORE_SLOT_POSITION and the
    # user_id param's docstring above. Silently skipped (never a 500/404)
    # if user_id doesn't match a real user — this endpoint stays usable
    # anonymously/defensively regardless of what's passed here. Deliberately
    # never cached — record_exposure/db.commit() below is a write side
    # effect that must run exactly once per real request, not be replayed
    # from a shared cache entry.
    if is_all and cursor is None and user_id and display_items:
        user_exists = (await db.execute(select(User.id).where(User.id == user_id))).scalar_one_or_none()
        if user_exists:
            candidate = await pick_candidate(db)
            if candidate is not None and candidate.id not in {c.id for c in display_items}:
                candidate_result = await db.execute(
                    select(StoryCluster)
                    .options(selectinload(StoryCluster.articles).selectinload(Article.source))
                    .where(StoryCluster.id == candidate.id)
                )
                candidate = candidate_result.scalar_one_or_none()
                if candidate is not None:
                    insert_at = min(EXPLORE_SLOT_POSITION - 1, len(display_items))
                    display_items = display_items[:insert_at] + [_cluster_to_list_out(candidate)] + display_items[insert_at:]
                    await record_exposure(db, candidate.id, user_id)
                    await db.commit()

    formatted_clusters = []
    seen_ids = set()
    for cluster_out in display_items:
        if cluster_out.id in seen_ids:
            continue
        seen_ids.add(cluster_out.id)
        formatted_clusters.append(cluster_out)

    return PaginatedClustersListOut(
        items=formatted_clusters,
        next_cursor=next_cursor,
        has_more=has_more,
    )

@app.get(f"{settings.API_V1_STR}/clusters/for-you", response_model=PaginatedClustersOut)
@limiter.limit("60/minute")
async def list_for_you_clusters(
    request: Request,
    user_id: str = Query(...),
    limit: int = Query(20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """
    Feed ranking redesign, piece 2: ranks a candidate window of recent
    clusters by the requesting user's own affinity_score (see
    app.services.affinity.score_clusters_for_user) — deliberately
    independent of "All Stories"'s headline_score/entity_boost, so
    personalization can never distort what counts as globally important.
    See the "Feed ranking redesign" design memory.

    A user with no read history yet gets affinity_score=0 for every
    candidate, so the ranking falls back to the candidate query's own order
    (headline_score, same as "All Stories") — deliberately no separate
    empty state for cold start.

    v1: no real pagination yet — returns the top `limit` of a bounded
    candidate window (most recent, highest headline_score clusters with
    entities). Real cursor-based pagination is a follow-up once there's
    enough usage to justify it.

    Registered ABOVE /clusters/{cluster_id} deliberately — FastAPI/Starlette
    matches routes in registration order, so a literal path segment like
    "for-you" must be declared before a path-parameter route that would
    otherwise capture it as cluster_id (this broke once already in prod;
    see git history).
    """
    user_result = await db.execute(select(User).where(User.id == user_id))
    if user_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="User not found")

    # Per-user (unlike /clusters, this genuinely varies per requester) —
    # this endpoint previously hydrated a 200-cluster candidate window with
    # full articles/content on every single request, uncached, to return
    # only `limit`. Same short-TTL pattern as /clusters/_cache_get below;
    # a user reloading "For You" repeatedly within the window now hits
    # Redis instead of re-scoring 200 clusters against Postgres each time.
    cache_key = f"cache:for-you:{user_id}:{limit}"
    cached = await _cache_get(cache_key)
    if cached is not None:
        return Response(content=cached, media_type="application/json")

    # Bounded candidate window so scoring stays cheap — affinity is scored
    # in Python (score_clusters_for_user), not SQL, since it has to unpack
    # each cluster's entities JSON and canonicalize them. Window cut from
    # 200 to 100 — score_clusters_for_user's inputs (entities, starred
    # sources) don't meaningfully benefit from a deeper window, and this
    # halves how many clusters' full articles get hydrated for a request
    # that only returns `limit` (<=50) of them.
    candidates_query = apply_feed_gate(
        select(StoryCluster)
        .options(selectinload(StoryCluster.articles).selectinload(Article.source))
        .where(
            listing_age_anchor() >= utc_now() - LISTING_MAX_AGE,
            StoryCluster.entities.isnot(None),
        )
    )
    candidates_result = await db.execute(
        candidates_query
        .order_by(desc(StoryCluster.headline_score), desc(StoryCluster.id))
        .limit(100)
    )
    candidates = candidates_result.scalars().all()

    scores = await score_clusters_for_user(db, user_id, candidates)
    ranked = sorted(candidates, key=lambda c: (scores.get(c.id, 0.0), c.headline_score), reverse=True)
    items = ranked[:limit]

    result_out = PaginatedClustersOut(
        items=[_cluster_to_out(c) for c in items],
        next_cursor=None,
        has_more=False,
    )
    await _cache_set(cache_key, result_out.model_dump_json())
    return result_out


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

    return _cluster_to_out(cluster)


@app.get(f"{settings.API_V1_STR}/clusters/{{cluster_id}}/related", response_model=RelatedClustersOut)
@limiter.limit("60/minute")
async def get_related_clusters(
    request: Request,
    cluster_id: int,
    sort: str = Query("relevance", pattern="^(relevance|time)$"),
    limit: int = Query(20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """Flat "related stories" list — see app/services/related_stories.py for
    what this does and does not guarantee (byproduct of the still-buggy
    story-graph experiment, see backend/docs/story-graph-design.md)."""
    cache_key = f"related:{cluster_id}:{sort}:{limit}"
    cached = await _cache_get(cache_key)
    if cached:
        return RelatedClustersOut.model_validate_json(cached)

    related, actor = await find_related_clusters(db, cluster_id, sort=sort, limit=limit)
    if actor is None and not related:
        # Distinguish "cluster not in the lookback window at all" (404) from
        # "found, but genuinely has no related stories" (200, empty list) —
        # cheaply, by checking existence rather than re-deriving from
        # find_related_clusters's internals.
        exists = await db.execute(select(StoryCluster.id).where(StoryCluster.id == cluster_id))
        if exists.scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail="Story cluster not found")

    cluster_ids = [c.id for c in related]
    articles_result = await db.execute(
        select(StoryCluster)
        .where(StoryCluster.id.in_(cluster_ids))
        .options(selectinload(StoryCluster.articles).selectinload(Article.source))
    )
    by_id = {c.id: c for c in articles_result.scalars().all()}
    items = [_cluster_to_out(by_id[cid]) for cid in cluster_ids if cid in by_id]

    result_out = RelatedClustersOut(items=items, actor=actor)
    await _cache_set(cache_key, result_out.model_dump_json())
    return result_out


def _validate_game_type(game_type: str) -> None:
    if game_type not in VALID_GAME_TYPES:
        raise HTTPException(status_code=422, detail=f"Unknown game_type '{game_type}'")


def _compute_streaks(completed_dates: set) -> tuple[int, int]:
    """current_streak_days = consecutive calendar days (any game_type) with
    at least one completed puzzle, ending today or yesterday (one day's
    grace before a streak is considered broken). longest_streak_days is the
    longest such run ever, over the same per-day union across all games."""
    if not completed_dates:
        return 0, 0

    today = utc_now().date()
    cursor = today if today in completed_dates else today - timedelta(days=1)
    current = 0
    while cursor in completed_dates:
        current += 1
        cursor -= timedelta(days=1)

    longest = 0
    run = 0
    prev = None
    for d in sorted(completed_dates):
        run = run + 1 if prev is not None and d == prev + timedelta(days=1) else 1
        longest = max(longest, run)
        prev = d

    return current, longest


@app.post(f"{settings.API_V1_STR}/users/{{user_id}}/games/{{game_type}}/start", status_code=200)
@limiter.limit("60/minute")
async def start_game_session(
    request: Request,
    user_id: str,
    game_type: str,
    payload: GameSessionRequest,
    db: AsyncSession = Depends(get_db),
):
    """Record that `user_id` opened `game_type`'s puzzle for `puzzle_date`.
    Upserted on the (user_id, game_type, puzzle_date) unique index so
    reopening the same day's puzzle doesn't inflate "games played" — only
    inserts a fresh row (completed=False) if one doesn't already exist;
    an existing row (whether or not already completed) is left untouched."""
    _validate_game_type(game_type)
    result = await db.execute(select(User).where(User.id == user_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="User not found")

    statement = pg_insert(GameSession).values(
        user_id=user_id, game_type=game_type, puzzle_date=payload.puzzle_date, completed=False,
    ).on_conflict_do_nothing(index_elements=["user_id", "game_type", "puzzle_date"])
    await db.execute(statement)
    await db.commit()
    return {"message": "Session started"}


@app.post(f"{settings.API_V1_STR}/users/{{user_id}}/games/{{game_type}}/complete", status_code=200)
@limiter.limit("60/minute")
async def complete_game_session(
    request: Request,
    user_id: str,
    game_type: str,
    payload: GameSessionRequest,
    db: AsyncSession = Depends(get_db),
):
    """Mark (user_id, game_type, puzzle_date) as completed — upserts so this
    works even if /start was never called (e.g. app was killed mid-puzzle
    and only the completion fired on relaunch)."""
    _validate_game_type(game_type)
    result = await db.execute(select(User).where(User.id == user_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="User not found")

    now = utc_now()
    statement = pg_insert(GameSession).values(
        user_id=user_id, game_type=game_type, puzzle_date=payload.puzzle_date,
        completed=True, completed_at=now,
        score=payload.score, completion_time_seconds=payload.completion_time_seconds,
        difficulty=payload.difficulty,
    ).on_conflict_do_update(
        index_elements=["user_id", "game_type", "puzzle_date"],
        set_={
            "completed": True, "completed_at": now,
            "score": payload.score, "completion_time_seconds": payload.completion_time_seconds,
            "difficulty": payload.difficulty,
        },
    )
    await db.execute(statement)
    await db.commit()
    return {"message": "Session completed"}


@app.get(f"{settings.API_V1_STR}/users/{{user_id}}/games/stats", response_model=GameStatsOut)
@limiter.limit("60/minute")
async def get_game_stats(request: Request, user_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.id == user_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="User not found")

    rows = await db.execute(
        select(
            GameSession.game_type,
            func.count(GameSession.id).label("played"),
            func.count(GameSession.id).filter(GameSession.completed.is_(True)).label("completed"),
            func.max(GameSession.score).filter(GameSession.completed.is_(True)).label("best_score"),
            func.avg(GameSession.completion_time_seconds).filter(GameSession.completed.is_(True)).label("avg_time"),
            func.max(GameSession.puzzle_date).filter(GameSession.completed.is_(True)).label("last_played"),
        )
        .where(GameSession.user_id == user_id)
        .group_by(GameSession.game_type)
    )
    by_game: dict[str, GameTypeStatsOut] = {}
    total_played = 0
    total_completed = 0
    most_played_game: Optional[str] = None
    most_played_count = 0
    for game_type, played, completed, best_score, avg_time, last_played in rows.all():
        by_game[game_type] = GameTypeStatsOut(
            played=played,
            completed=completed,
            attempted_incomplete=played - completed,
            best_score=best_score,
            avg_completion_time_seconds=int(avg_time) if avg_time is not None else None,
            last_played_date=last_played,
        )
        total_played += played
        total_completed += completed
        if played > most_played_count:
            most_played_count = played
            most_played_game = game_type

    score_sum_result = await db.execute(
        select(func.coalesce(func.sum(GameSession.score), 0))
        .where(GameSession.user_id == user_id, GameSession.completed.is_(True))
    )
    score_sum = score_sum_result.scalar_one()
    xp = total_completed * 10 + score_sum
    level = 1 + xp // 100
    xp_to_next_level = 100 - (xp % 100)

    dates_result = await db.execute(
        select(GameSession.puzzle_date)
        .where(GameSession.user_id == user_id, GameSession.completed.is_(True))
        .distinct()
    )
    current_streak_days, longest_streak_days = _compute_streaks(
        {row[0] for row in dates_result.all()}
    )

    return GameStatsOut(
        total_played=total_played,
        total_completed=total_completed,
        most_played_game=most_played_game,
        current_streak_days=current_streak_days,
        longest_streak_days=longest_streak_days,
        level=level,
        xp=xp,
        xp_to_next_level=xp_to_next_level,
        by_game=by_game,
    )


@app.post(f"{settings.API_V1_STR}/users/{{user_id}}/read-events", status_code=200)
@limiter.limit("120/minute")
async def record_read_event(
    request: Request,
    user_id: str,
    payload: ReadEventRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Feed ranking redesign, piece 2 instrumentation. Upserts one row per
    (user_id, event_id) — see ReadEvent's docstring. The client calls this
    twice per story view: once on open (dwell_ms/scroll_depth_pct omitted)
    to record opened_at, and once on close (both populated) to record
    engagement. Only the close call (dwell_ms present) updates
    user_entity_affinity — see app.services.affinity.record_engagement.
    """
    user_result = await db.execute(select(User).where(User.id == user_id))
    if user_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="User not found")

    cluster_result = await db.execute(select(StoryCluster).where(StoryCluster.id == payload.cluster_id))
    cluster = cluster_result.scalar_one_or_none()
    if cluster is None:
        raise HTTPException(status_code=404, detail="Cluster not found")

    statement = pg_insert(ReadEvent).values(
        user_id=user_id, cluster_id=payload.cluster_id, event_id=payload.event_id,
        dwell_ms=payload.dwell_ms, scroll_depth_pct=payload.scroll_depth_pct,
        event_type=payload.event_type,
    ).on_conflict_do_update(
        # Was constraint="uq_read_events_user_event", which Postgres rejects:
        # ReadEvent declares a unique Index, not a UniqueConstraint, so every
        # call to this endpoint raised and returned 500. The client swallows
        # the failure (NewsApiClient.recordReadEvent catches and returns
        # false), so it went unnoticed — no read event has ever been stored.
        index_elements=["user_id", "event_id"],
        set_={
            "dwell_ms": payload.dwell_ms, "scroll_depth_pct": payload.scroll_depth_pct,
            "event_type": payload.event_type, "updated_at": utc_now(),
        },
    )
    await db.execute(statement)

    # Placeholder engagement weight (v1): a completed close call counts as
    # 1.0 regardless of actual dwell/scroll magnitude. Piece 3's
    # dwell-relative-to-article-length formula isn't built yet — that's the
    # explore-slot bandit's job, not this endpoint's. See the "Feed ranking
    # redesign" design memory and app.services.affinity.
    # Framing-panel opens and summary expansions are measured, not scored:
    # they say the reader is curious about how a story is being covered, which
    # isn't the same signal as topic affinity and shouldn't steer the feed.
    if payload.dwell_ms is not None and payload.event_type == "read":
        await record_engagement(db, user_id, cluster, engagement_weight=1.0)

    await db.commit()
    return {"message": "Recorded"}


# ─────────────────────────────────────────────────────────────────────────────
# Donations
#
# The app opens an external Razorpay payment page in a browser tab; Razorpay
# calls the webhook below when a payment is captured. Nothing here grants
# anything — there is no entitlement to grant, by design. See app/models.py's
# Donation, and keep it that way: the moment a donation unlocks app
# functionality, this stops being a donation and has to move to Play Billing.
# ─────────────────────────────────────────────────────────────────────────────


@app.post(f"{settings.API_V1_STR}/donations/link", response_model=DonationLinkResponse)
@limiter.limit("10/minute")
async def create_donation_link(
    request: Request,
    payload: DonationLinkRequest,
    db: AsyncSession = Depends(get_db),
):
    """Mints a Razorpay Payment Link for a donation and hands back its URL.

    Rate limited hard: each call hits Razorpay's API and creates a real object
    in our account, so this is the one donation path an anonymous caller can
    make us do work on.
    """
    donor_id = payload.user_id
    if donor_id:
        known = await db.execute(select(User.id).where(User.id == donor_id))
        if known.scalar_one_or_none() is None:
            donor_id = None

    url = await create_payment_link(payload.amount_paise, donor_id)
    if url is None:
        raise HTTPException(status_code=503, detail="Could not start a donation right now")
    return DonationLinkResponse(url=url)


@app.post("/payments/razorpay/webhook", status_code=200)
@limiter.limit("120/minute")
async def razorpay_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Records a captured payment.

    The signature is checked against the raw body before anything is parsed —
    an unverified body is attacker-controlled input, so it must not reach the
    JSON parser, let alone the database. Idempotent on provider_payment_id
    because Razorpay retries until it gets a 2xx, and a retry must not double
    count the money.
    """
    if not settings.RAZORPAY_WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="Donations are not configured")

    raw = await request.body()
    if not signature_matches(raw, request.headers.get("X-Razorpay-Signature", ""), settings.RAZORPAY_WEBHOOK_SECRET):
        raise HTTPException(status_code=400, detail="Invalid signature")

    try:
        payment = parse_captured_payment(json.loads(raw))
    except MalformedWebhook:
        raise HTTPException(status_code=400, detail="Malformed payment entity")
    except ValueError:
        raise HTTPException(status_code=400, detail="Malformed payload")
    if payment is None:
        return {"message": "Ignored"}

    # An unknown or stale donor id is stored as NULL rather than rejected:
    # losing attribution is a much smaller problem than losing the record of
    # a payment that has already been taken.
    donor_id = payment.user_id
    if donor_id:
        known = await db.execute(select(User.id).where(User.id == donor_id))
        if known.scalar_one_or_none() is None:
            donor_id = None

    statement = pg_insert(Donation).values(
        user_id=donor_id,
        amount_paise=payment.amount_paise,
        currency=payment.currency,
        provider="razorpay",
        provider_payment_id=payment.provider_payment_id,
        status="captured",
    # index_elements, not constraint=: uniqueness here is a plain unique index
    # (see Donation.__table_args__), and Postgres only accepts ON CONFLICT ON
    # CONSTRAINT for a real constraint — naming an index there raises
    # "constraint does not exist" at execution time, not at import.
    ).on_conflict_do_nothing(index_elements=["provider_payment_id"])
    await db.execute(statement)
    await db.commit()
    return {"message": "Recorded"}


def _require_admin(request: Request) -> None:
    """Reuses the existing admin cookie session (app/admin_session.py) rather
    than adding a second credential path for two read-only JSON endpoints."""
    if session_csrf(request) is None:
        raise HTTPException(status_code=403, detail="Admin sign-in required")


@app.get("/admin/donations")
async def admin_donations(request: Request, db: AsyncSession = Depends(get_db)):
    """Donation totals, all-time and last 30 days."""
    _require_admin(request)
    cutoff = utc_now() - timedelta(days=30)

    async def totals(*where):
        result = await db.execute(
            select(func.count(Donation.id), func.coalesce(func.sum(Donation.amount_paise), 0))
            .where(Donation.status == "captured", *where)
        )
        count, paise = result.one()
        return {"count": count, "total_inr": round((paise or 0) / 100, 2)}

    return {
        "all_time": await totals(),
        "last_30_days": await totals(Donation.created_at >= cutoff),
        "distinct_donors": (await db.execute(
            select(func.count(func.distinct(Donation.user_id)))
            .where(Donation.status == "captured", Donation.user_id.isnot(None))
        )).scalar_one(),
    }


@app.get("/admin/engagement")
async def admin_engagement(request: Request, db: AsyncSession = Depends(get_db)):
    """Weekly framing-panel engagement, the other half of the donation signal.

    Donations alone can't distinguish "readers don't value the framing angle"
    from "they value it but won't pay for news" — those point at opposite
    strategies. Pairing conversion with how many active readers actually open
    the framing panel is what separates them.
    """
    _require_admin(request)
    week = func.date_trunc("week", ReadEvent.opened_at).label("week")

    result = await db.execute(
        select(
            week,
            func.count(func.distinct(ReadEvent.user_id)).label("active_users"),
            func.count(func.distinct(
                case((ReadEvent.event_type == "framing_view", ReadEvent.user_id))
            )).label("framing_users"),
            func.count(case((ReadEvent.event_type == "framing_view", 1))).label("framing_views"),
        )
        .where(ReadEvent.opened_at >= utc_now() - timedelta(days=84))
        .group_by(week).order_by(desc(week))
    )
    return {
        "weeks": [
            {
                "week": row.week.date().isoformat(),
                "active_users": row.active_users,
                "framing_users": row.framing_users,
                "framing_views": row.framing_views,
                "framing_reach_pct": round(100 * row.framing_users / row.active_users, 1) if row.active_users else 0.0,
            }
            for row in result
        ]
    }


@app.post(f"{settings.API_V1_STR}/users/{{user_id}}/saved-stories", status_code=200)
@limiter.limit("60/minute")
async def save_story(
    request: Request,
    user_id: str,
    payload: SaveStoryRequest,
    db: AsyncSession = Depends(get_db),
):
    """Bookmarks a cluster for user_id. Upserted on (user_id, cluster_id) —
    re-saving an already-saved cluster is idempotent and does not bump
    saved_at, so unsave-then-resave ordering stays predictable."""
    user_result = await db.execute(select(User).where(User.id == user_id))
    if user_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="User not found")

    cluster_result = await db.execute(select(StoryCluster).where(StoryCluster.id == payload.cluster_id))
    if cluster_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Cluster not found")

    statement = pg_insert(SavedStory).values(
        user_id=user_id, cluster_id=payload.cluster_id,
    ).on_conflict_do_nothing(index_elements=["user_id", "cluster_id"])
    await db.execute(statement)
    await db.commit()
    return {"message": "Saved"}


@app.delete(f"{settings.API_V1_STR}/users/{{user_id}}/saved-stories/{{cluster_id}}", status_code=200)
@limiter.limit("60/minute")
async def unsave_story(
    request: Request,
    user_id: str,
    cluster_id: int,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SavedStory).where(SavedStory.user_id == user_id, SavedStory.cluster_id == cluster_id)
    )
    saved = result.scalar_one_or_none()
    if saved is not None:
        await db.delete(saved)
        await db.commit()
    return {"message": "Unsaved"}


@app.post(f"{settings.API_V1_STR}/users/{{user_id}}/clusters/{{cluster_id}}/report", status_code=201)
@limiter.limit("20/minute")
async def report_story(
    request: Request,
    user_id: str,
    cluster_id: int,
    payload: ReportStoryRequest,
    db: AsyncSession = Depends(get_db),
):
    user_result = await db.execute(select(User).where(User.id == user_id))
    if user_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="User not found")

    cluster_result = await db.execute(select(StoryCluster).where(StoryCluster.id == cluster_id))
    if cluster_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Cluster not found")

    db.add(StoryReport(cluster_id=cluster_id, user_id=user_id, reason=payload.reason, note=payload.note))
    await db.commit()
    return {"message": "Reported"}


@app.get(f"{settings.API_V1_STR}/users/{{user_id}}/saved-stories", response_model=SavedStoriesOut)
@limiter.limit("60/minute")
async def list_saved_stories(
    request: Request,
    user_id: str,
    limit: int = Query(20, ge=1, le=50),
    cursor: Optional[int] = Query(None, description="Cursor for pagination (a saved_stories.id)"),
    db: AsyncSession = Depends(get_db),
):
    user_result = await db.execute(select(User).where(User.id == user_id))
    if user_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="User not found")

    query = (
        select(SavedStory)
        .options(selectinload(SavedStory.cluster).selectinload(StoryCluster.articles).selectinload(Article.source))
        .where(SavedStory.user_id == user_id)
        .order_by(desc(SavedStory.id))
    )
    if cursor:
        query = query.where(SavedStory.id < cursor)
    query = query.limit(limit + 1)

    result = await db.execute(query)
    rows = result.scalars().all()

    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = str(items[-1].id) if has_more and items else None

    formatted = [
        SavedStoryOut(saved_at=row.saved_at, cluster=_cluster_to_out(row.cluster))
        for row in items
    ]

    return SavedStoriesOut(items=formatted, next_cursor=next_cursor, has_more=has_more)


@app.post(f"{settings.API_V1_STR}/users/{{user_id}}/sources/{{source_id}}/star", status_code=200)
@limiter.limit("60/minute")
async def star_source(
    request: Request,
    user_id: str,
    source_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Stars source_id for user_id — boosts ranking in /clusters/for-you
    only (see app.services.affinity.score_clusters_for_user), never in
    "All Stories". Upserted on (user_id, source_id), idempotent."""
    user_result = await db.execute(select(User).where(User.id == user_id))
    if user_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="User not found")

    source_result = await db.execute(select(Source).where(Source.id == source_id))
    if source_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Source not found")

    statement = pg_insert(UserSourceFollow).values(
        user_id=user_id, source_id=source_id,
    ).on_conflict_do_nothing(index_elements=["user_id", "source_id"])
    await db.execute(statement)
    await db.commit()
    return {"message": "Starred"}


@app.delete(f"{settings.API_V1_STR}/users/{{user_id}}/sources/{{source_id}}/star", status_code=200)
@limiter.limit("60/minute")
async def unstar_source(
    request: Request,
    user_id: str,
    source_id: int,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(UserSourceFollow).where(
            UserSourceFollow.user_id == user_id, UserSourceFollow.source_id == source_id,
        )
    )
    follow = result.scalar_one_or_none()
    if follow is not None:
        await db.delete(follow)
        await db.commit()
    return {"message": "Unstarred"}


@app.get(f"{settings.API_V1_STR}/users/{{user_id}}/sources/starred", response_model=StarredSourcesOut)
@limiter.limit("60/minute")
async def list_starred_sources(
    request: Request,
    user_id: str,
    db: AsyncSession = Depends(get_db),
):
    user_result = await db.execute(select(User).where(User.id == user_id))
    if user_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="User not found")

    result = await db.execute(
        select(Source)
        .join(UserSourceFollow, UserSourceFollow.source_id == Source.id)
        .where(UserSourceFollow.user_id == user_id)
        .order_by(Source.name)
    )
    sources = result.scalars().all()
    return StarredSourcesOut(items=[SourceOut.model_validate(s) for s in sources])


@app.post(f"{settings.API_V1_STR}/users/{{user_id}}/sources/{{source_id}}/block", status_code=200)
@limiter.limit("60/minute")
async def block_source(
    request: Request,
    user_id: str,
    source_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Blocks source_id for user_id — client filters this source's stories
    out of every list it renders (feed, search, saved, related,
    notifications). Purely a client-side preference; has no effect on
    server-side ranking or feed queries. Upserted on (user_id, source_id),
    idempotent."""
    user_result = await db.execute(select(User).where(User.id == user_id))
    if user_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="User not found")

    source_result = await db.execute(select(Source).where(Source.id == source_id))
    if source_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Source not found")

    statement = pg_insert(UserSourceBlock).values(
        user_id=user_id, source_id=source_id,
    ).on_conflict_do_nothing(index_elements=["user_id", "source_id"])
    await db.execute(statement)
    await db.commit()
    return {"message": "Blocked"}


@app.delete(f"{settings.API_V1_STR}/users/{{user_id}}/sources/{{source_id}}/block", status_code=200)
@limiter.limit("60/minute")
async def unblock_source(
    request: Request,
    user_id: str,
    source_id: int,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(UserSourceBlock).where(
            UserSourceBlock.user_id == user_id, UserSourceBlock.source_id == source_id,
        )
    )
    block = result.scalar_one_or_none()
    if block is not None:
        await db.delete(block)
        await db.commit()
    return {"message": "Unblocked"}


@app.get(f"{settings.API_V1_STR}/users/{{user_id}}/sources/blocked", response_model=BlockedSourcesOut)
@limiter.limit("60/minute")
async def list_blocked_sources(
    request: Request,
    user_id: str,
    db: AsyncSession = Depends(get_db),
):
    user_result = await db.execute(select(User).where(User.id == user_id))
    if user_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="User not found")

    result = await db.execute(
        select(Source)
        .join(UserSourceBlock, UserSourceBlock.source_id == Source.id)
        .where(UserSourceBlock.user_id == user_id)
        .order_by(Source.name)
    )
    sources = result.scalars().all()
    return BlockedSourcesOut(items=[SourceOut.model_validate(s) for s in sources])
