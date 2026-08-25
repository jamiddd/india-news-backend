import asyncio
import logging
import random
from datetime import date, datetime, time, timedelta
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional, List
from fastapi import FastAPI, Depends, HTTPException, Query, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
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
from app.models import Source, Article, StoryCluster, User, DeviceToken, DailyCrossword, DailyPoll, PollOption, PollVote, GameSession, ReadEvent, SavedStory, UserSourceFollow, utc_now
from app.schemas import (
    SourceOut, StoryClusterOut, ArticleOut, PaginatedClustersOut, RelatedClustersOut,
    UserAuthRequest, UserAuthResponse, UserPreferences, AccountDeleteRequest,
    DeviceTokenRegisterRequest,
    DailyCrosswordOut, CrosswordCheckRequest, CrosswordCheckResponse,
    CrosswordRevealRequest, CrosswordRevealResponse,
    DailySudokuOut,
    DailyWordSearchOut,
    DailySpellingBeeOut, DailyWordLadderOut, DailyQuizOut,
    WordOfTheDayOut, QuoteOfTheDayOut, OnThisDayOut, DailyHoroscopeOut, DailyPollOut, PollVoteRequest,
    GameSessionRequest, GameStatsOut, GameTypeStatsOut, VALID_GAME_TYPES,
    ReadEventRequest,
    SaveStoryRequest, SavedStoryOut, SavedStoriesOut,
    StarredSourcesOut,
)
from app.services.affinity import record_engagement, score_clusters_for_user
from app.services.explore_bandit import pick_candidate, record_exposure, EXPLORE_PROMOTED_BOOST, EXPLORE_SLOT_POSITION
from uuid import uuid4
from app.services.poller import poll_all_sources
from app.services.topic_filters import CONTENT_GATED_CATEGORIES, keyword_regex
from app.services.enrichment import enrich_cluster_with_ai
from app.services.related_stories import find_related_clusters
from scripts.enrich_all_clusters import enrich_clusters
from app.services.crossword import get_or_create_puzzle, india_today
from app.services.sudoku import get_or_create_sudoku
from app.services.word_search import get_or_create_word_search
from app.services.daily_games import get_or_create_daily_games
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

STATIC_DIR = Path(__file__).parent / "static"

# Short-TTL read cache for the hot list endpoints (/clusters, /search).
# Redis was already provisioned for rate limiting (see `limiter` below) but
# sat otherwise idle for reads — every request hit Postgres directly even
# though the underlying data only actually changes once per poll cycle
# (~15 min). 30s is short enough that a poll-triggered update is visible
# almost immediately, while still collapsing the realistic case of many
# concurrent clients requesting the same (category, cursor) page within a
# few seconds of each other into one DB query. Deliberately fails open on
# any Redis error (network blip, Redis restart) — caching is a performance
# optimization, not a correctness dependency, so a cache miss/error just
# means "hit the DB like before", never a request failure.
CACHE_TTL_SECONDS = 30

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

# Clusters older than this (by first_seen_at — when the story actually first
# appeared, not last_updated_at) never surface in listings, in either feed.
# Added after finding stale singleton crypto clusters (some from 2022)
# ranking as if fresh: their last_updated_at had been bulk-touched to "now"
# by an out-of-band write unrelated to any real new coverage, which both
# sorted them above genuinely current stories in category tabs (ordered by
# last_updated_at) and inflated their headline_score's recency-decay term in
# the "All" feed. first_seen_at is set once at cluster creation and never
# rewritten by anything, so it's the one timestamp immune to that class of
# corruption — filtering on it is a hard backstop regardless of what causes
# last_updated_at to drift.
LISTING_MAX_AGE = timedelta(days=4)

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
    """Builds a StoryClusterOut from an ORM StoryCluster, filling
    ArticleOut.source_name (not a plain column — comes from art.source.name)
    by hand. NOT a plain StoryClusterOut.model_validate(cluster): that fails
    on the missing source_name field. Requires cluster.articles' .source to
    already be loaded (selectinload), same as every existing call site."""
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
            image_url=art.image_url,
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


@app.get("/")
@limiter.exempt
async def root(request: Request):
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
    bee, _, _ = await get_or_create_daily_games(db, resolve_puzzle_date(date))
    return {"date": bee.puzzle_date, "letters": bee.letters, "center_letter": bee.center_letter, "words": bee.words}


@app.get(f"{settings.API_V1_STR}/word-ladder/daily", response_model=DailyWordLadderOut)
@limiter.limit("30/minute")
async def daily_word_ladder(request: Request, date: str | None = Query(None), db: AsyncSession = Depends(get_db)):
    _, ladder, _ = await get_or_create_daily_games(db, resolve_puzzle_date(date))
    return {
        "date": ladder.puzzle_date,
        "start_word": ladder.start_word,
        "target_word": ladder.target_word,
        "allowed_words": ladder.allowed_words,
        "optimal_steps": ladder.optimal_steps,
    }


@app.get(f"{settings.API_V1_STR}/quiz/daily", response_model=DailyQuizOut)
@limiter.limit("30/minute")
async def daily_quiz(request: Request, date: str | None = Query(None), db: AsyncSession = Depends(get_db)):
    _, _, quiz = await get_or_create_daily_games(db, resolve_puzzle_date(date))
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

@app.post(f"{settings.API_V1_STR}/ingest/enrich")
@limiter.limit("5/hour")
async def trigger_enrich(
    request: Request,
    background_tasks: BackgroundTasks,
    since_days: float = Query(2.0, description="Enrich clusters touched in the last N days"),
    force_all: bool = Query(False, description="Re-enrich even already-ai_enriched clusters (use for one-off backfills, not the recurring timer)"),
):
    # enrich_clusters() opens its own DB session internally rather than
    # taking the request-scoped `db` (contrast trigger_poll above, which
    # passes its request-scoped session into the background task) — a
    # background task must not depend on a session FastAPI may already be
    # closing once the response is sent.
    background_tasks.add_task(enrich_clusters, None, force_all, since_days)
    return {
        "message": f"Enrichment triggered in background (since_days={since_days}, force_all={force_all})."
    }

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
    cache_key = f"cache:search:{q}:{limit}:{cursor or ''}"
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

    result_out = PaginatedClustersOut(
        items=formatted_clusters,
        next_cursor=next_cursor,
        has_more=has_more
    )
    await _cache_set(cache_key, result_out.model_dump_json())
    return result_out

@app.get(f"{settings.API_V1_STR}/clusters", response_model=PaginatedClustersOut)
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
    db: AsyncSession = Depends(get_db)
):
    cache_key = f"cache:clusters:{category or 'all'}:{limit}:{cursor or ''}:{source_weights or ''}:{seed or ''}:{user_id or ''}:{min_sources or ''}"
    cached = await _cache_get(cache_key)
    if cached is not None:
        return Response(content=cached, media_type="application/json")

    is_all = not category or category.lower() == "all"
    weights = _parse_source_weights(source_weights) if is_all else {}
    # Feed ranking redesign, piece 3: a promoted explore candidate gets a
    # real, live multiplier here — not a shadow signal like piece 1's
    # entity_boost, this is the actual mechanism that rescues a buried
    # story for everyone once it's earned it. See app.services.explore_bandit.
    explore_boost_expr = case(
        (StoryCluster.explore_status == "promoted", EXPLORE_PROMOTED_BOOST), else_=1.0
    )

    query = select(StoryCluster).options(
        selectinload(StoryCluster.articles).selectinload(Article.source)
    ).where(StoryCluster.first_seen_at >= utc_now() - LISTING_MAX_AGE)

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

    # Reorder (never refilter/repaginate) a fresh All Stories first page for
    # display, so reloading rotates the feed instead of returning the exact
    # same order every time headline_score hasn't recomputed yet (it only
    # updates once per poll cycle — see poller.py). next_cursor above is
    # already locked in from the real deterministic order, so this can't
    # skip or duplicate clusters across pages — it only shuffles what's
    # already been decided will appear on *this* page.
    if is_all and seed and cursor is None and items:
        display_items = _weighted_shuffle(
            items,
            weight_fn=lambda c: c.headline_score * max(
                (weights.get(a.source_id, 1.0) for a in c.articles), default=1.0
            ) * (EXPLORE_PROMOTED_BOOST if c.explore_status == "promoted" else 1.0),
            seed=seed,
        )
    else:
        display_items = items

    # Feed ranking redesign, piece 3: explore-slot bandit. Only on a fresh,
    # logged-in All Stories first page — see EXPLORE_SLOT_POSITION and the
    # user_id param's docstring above. Silently skipped (never a 500/404)
    # if user_id doesn't match a real user — this endpoint stays usable
    # anonymously/defensively regardless of what's passed here.
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
                    display_items = display_items[:insert_at] + [candidate] + display_items[insert_at:]
                    await record_exposure(db, candidate.id, user_id)
                    await db.commit()

    formatted_clusters = []
    seen_ids = set()
    for cluster in display_items:
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

    result_out = PaginatedClustersOut(
        items=formatted_clusters,
        next_cursor=next_cursor,
        has_more=has_more
    )
    await _cache_set(cache_key, result_out.model_dump_json())
    return result_out

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

    # Bounded candidate window so scoring stays cheap — affinity is scored
    # in Python (score_clusters_for_user), not SQL, since it has to unpack
    # each cluster's entities JSON and canonicalize them.
    candidates_result = await db.execute(
        select(StoryCluster)
        .options(selectinload(StoryCluster.articles).selectinload(Article.source))
        .where(
            StoryCluster.first_seen_at >= utc_now() - LISTING_MAX_AGE,
            StoryCluster.entities.isnot(None),
        )
        .order_by(desc(StoryCluster.headline_score), desc(StoryCluster.id))
        .limit(200)
    )
    candidates = candidates_result.scalars().all()

    scores = await score_clusters_for_user(db, user_id, candidates)
    ranked = sorted(candidates, key=lambda c: (scores.get(c.id, 0.0), c.headline_score), reverse=True)
    items = ranked[:limit]

    return PaginatedClustersOut(
        items=[_cluster_to_out(c) for c in items],
        next_cursor=None,
        has_more=False,
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
    ).on_conflict_do_update(
        constraint="uq_read_events_user_event",
        set_={"dwell_ms": payload.dwell_ms, "scroll_depth_pct": payload.scroll_depth_pct, "updated_at": utc_now()},
    )
    await db.execute(statement)

    # Placeholder engagement weight (v1): a completed close call counts as
    # 1.0 regardless of actual dwell/scroll magnitude. Piece 3's
    # dwell-relative-to-article-length formula isn't built yet — that's the
    # explore-slot bandit's job, not this endpoint's. See the "Feed ranking
    # redesign" design memory and app.services.affinity.
    if payload.dwell_ms is not None:
        await record_engagement(db, user_id, cluster, engagement_weight=1.0)

    await db.commit()
    return {"message": "Recorded"}


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
