"""Trending search terms, for the Explainers admin dashboard's "Suggested"
strip (see admin_explainers.py). v1 scope only — logs raw query terms from
the existing GET /search endpoint into a weekly Redis sorted set; there is no
reader-submission queue yet (see the Explainers feature plan/memory).

Best-effort throughout: a Redis error here must never fail a search request,
and a week with no logged searches just means an empty suggestions strip.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.redis_client import get_redis_client

logger = logging.getLogger(__name__)

TTL_SECONDS = 21 * 24 * 3600  # ~3 weeks, so last week's terms are still readable early this week


def _week_key(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    iso_year, iso_week, _ = now.isocalendar()
    return f"trending:search:{iso_year}-W{iso_week:02d}"


async def log_search_query(q: str) -> None:
    """Count one search for `q` (case-folded, trimmed) toward this week's
    trending terms. Called from GET /api/v1/search after the query is read."""
    term = q.strip().lower()
    if not term:
        return
    try:
        client = get_redis_client()
        key = _week_key()
        await client.zincrby(key, 1.0, term)
        await client.expire(key, TTL_SECONDS)
    except Exception as e:  # noqa: BLE001 - logging a search term must never fail the search itself
        logger.warning("could not log trending search term: %s", e)


async def get_trending_terms(limit: int = 5) -> list[tuple[str, int]]:
    """[(term, count), ...] for this week so far, highest count first. Admin
    turns a raw term into an actual question by hand — no NLP in v1."""
    try:
        client = get_redis_client()
        raw = await client.zrevrange(_week_key(), 0, limit - 1, withscores=True)
    except Exception as e:  # noqa: BLE001
        logger.warning("could not read trending search terms: %s", e)
        return []
    return [(term.decode() if isinstance(term, bytes) else term, int(score)) for term, score in raw]
