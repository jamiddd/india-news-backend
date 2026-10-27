"""GET /api/v1/launcher/feed — the news feed page of the Spot launcher.

One ranked "top stories" feed, same ordering as the News app's All feed
(headline_score, gated and age-limited) but without per-user weights, the
seed shuffle or the explore slot, so one cached page serves every client.
See docs/launcher-feed-spec.md in the spot repo for the contract.

Built as a factory so the shared slowapi limiter (created in main.py) can be
injected without importing main.py, which would be circular.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import desc, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db
from app.launcher_schemas import LauncherFeedOut
from app.models import Article, StoryCluster, utc_now
from app.redis_client import get_redis_client
from app.services.crossword import india_today
from app.services.feed_gate import (
    LISTING_MAX_AGE,
    apply_feed_gate,
    gate_cache_marker,
    listing_age_anchor,
)
from app.services.launcher_feed import (
    apply_promos,
    cluster_to_story_item,
    decode_cursor,
    encode_cursor,
)

logger = logging.getLogger(__name__)

# Short: the underlying data changes once per poll cycle (~15 min), and this
# page is shared by every launcher, so even 90s collapses almost all traffic.
LAUNCHER_FEED_CACHE_TTL_SECONDS = 90
DEFAULT_LIMIT = 15
MAX_LIMIT = 30


async def _cache_get(key: str) -> Optional[str]:
    try:
        return await get_redis_client().get(key)
    except Exception:
        return None


async def _cache_set(key: str, value: str, ttl: int) -> None:
    try:
        await get_redis_client().setex(key, ttl, value)
    except Exception:
        pass


MAX_DISMISSED_IDS = 20
MAX_DISMISSED_ID_LENGTH = 40


def _parse_dismissed(raw: Optional[str]) -> list[str]:
    """'a,b,c' -> ids. Bounded and trimmed; junk is dropped, never an error."""
    if not raw:
        return []
    ids = [part.strip() for part in raw.split(",")]
    return [i for i in ids if i and len(i) <= MAX_DISMISSED_ID_LENGTH][:MAX_DISMISSED_IDS]


def _with_promos(
    page: LauncherFeedOut,
    cursor: Optional[str],
    news_installed: bool,
    session_age_days: Optional[int],
    dismissed: Optional[str],
) -> LauncherFeedOut:
    """The stories-only page with this request's promos merged in. Works on a
    copy: `page` may be the object just cached/deserialised."""
    stories = [item.model_dump() for item in page.items]
    merged = apply_promos(
        stories,
        is_first_page=not cursor,
        news_installed=news_installed,
        session_age_days=session_age_days,
        dismissed=_parse_dismissed(dismissed),
        today=india_today(),
    )
    return LauncherFeedOut(
        items=merged,
        next_cursor=page.next_cursor,
        has_more=page.has_more,
        generated_at=page.generated_at,
    )


def create_launcher_feed_router(limiter) -> APIRouter:
    router = APIRouter()

    @router.get(f"{settings.API_V1_STR}/launcher/feed", response_model=LauncherFeedOut)
    @limiter.limit("120/minute")
    async def launcher_feed(
        request: Request,
        limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
        cursor: Optional[str] = Query(None, description="Opaque cursor from the previous page"),
        news_installed: bool = Query(
            False, description="True when the News app is installed on the device: no promo items are returned"
        ),
        session_age_days: Optional[int] = Query(
            None, ge=0, le=36500,
            description="Days since the launcher was installed. Promos need >= 2; omitted means no promos",
        ),
        dismissed: Optional[str] = Query(
            None, max_length=1000, description="Comma-separated promo ids the user dismissed"
        ),
        db: AsyncSession = Depends(get_db),
    ):
        # X-Client is for logs/metrics only; nothing is rejected on it. Note
        # the launcher must NOT send X-Client-Version: the News app's
        # min-version middleware (main.py) would 426 it against the News
        # app's versionCode floor. It sends X-Client-Build instead.
        logger.info(
            "launcher_feed client=%s build=%s",
            request.headers.get("X-Client", "-"),
            request.headers.get("X-Client-Build", "-"),
        )

        # The cached page is stories only. Promos are applied per request, after
        # this, so one cache entry serves every client (see _with_promos).
        # v2: story items gained `teaser`.
        cache_key = f"cache:launcher:v2:{gate_cache_marker()}:{limit}:{cursor or ''}"
        cached = await _cache_get(cache_key)
        if cached is not None:
            return _with_promos(
                LauncherFeedOut.model_validate_json(cached),
                cursor, news_installed, session_age_days, dismissed,
            )

        query = (
            select(StoryCluster)
            .options(selectinload(StoryCluster.articles).selectinload(Article.source))
            .where(listing_age_anchor() >= utc_now() - LISTING_MAX_AGE)
        )
        query = apply_feed_gate(query)
        query = query.order_by(desc(StoryCluster.headline_score), desc(StoryCluster.id))

        decoded = decode_cursor(cursor)
        if decoded is not None:
            score, cluster_id = decoded
            query = query.where(tuple_(StoryCluster.headline_score, StoryCluster.id) < (score, cluster_id))

        # Fetch extra so clusters that map to no card (no articles) do not
        # shrink the page below `limit` when more rows exist.
        result = await db.execute(query.limit(limit + 1))
        clusters = result.scalars().all()

        has_more = len(clusters) > limit
        page = clusters[:limit]

        items = []
        now = utc_now()
        for cluster in page:
            item = cluster_to_story_item(cluster, now)
            if item is not None:
                items.append(item)

        next_cursor = None
        if has_more and page:
            last = page[-1]
            next_cursor = encode_cursor(last.headline_score, last.id)

        out = LauncherFeedOut(
            items=items,
            next_cursor=next_cursor,
            has_more=has_more,
            generated_at=now,
        )
        await _cache_set(cache_key, out.model_dump_json(), LAUNCHER_FEED_CACHE_TTL_SECONDS)
        return _with_promos(out, cursor, news_installed, session_age_days, dismissed)

    return router
