"""Quote of the Day background photos, served from our own Supabase Storage
bucket instead of a third-party photo API.

The previous implementation called Unsplash's /photos/random on the day a
DailyEditorial row was created, stored the returned CDN url on the row, and
retried that call on *every* request for any date whose fetch had failed —
so a missing key or a rate limit turned into one outbound Unsplash request
per app open. It also left the image bytes on Unsplash's CDN, which their
API guidelines require but which means the feature breaks the moment that
key is revoked or the demo tier is exceeded.

Now: a curated set of images is uploaded once to a public Supabase Storage
bucket (same Supabase project as the database, so no new vendor), and the
day's background is picked from that set by date ordinal. Clients hit the
public storage url as many times as they like. The only outbound call this
module ever makes is listing the bucket, and that is Redis-cached — shared
by both droplets behind the load balancer, so it is roughly one list per
LIST_CACHE_TTL_SECONDS across the whole fleet, not one per request.

Absent config or an empty bucket = returns None, and the app falls back to
its plain gradient background exactly as it does today.
"""
from __future__ import annotations

import json
import logging
from datetime import date

import httpx

from app.config import settings
from app.redis_client import get_redis_client

logger = logging.getLogger(__name__)

LIST_CACHE_KEY = "editorial:backgrounds:objects"
LIST_CACHE_TTL_SECONDS = 6 * 60 * 60

# Supabase Storage lists at most `limit` objects per call. The curated set is
# a few dozen images; a single page is deliberately enough.
LIST_PAGE_SIZE = 200

IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".avif")


def _configured() -> bool:
    return bool(settings.SUPABASE_URL and settings.SUPABASE_SERVICE_KEY)


def project_base_url() -> str:
    """The bare project origin, e.g. https://abc.supabase.co.

    Tolerates the value the dashboard's Data API page actually hands you,
    which is the REST endpoint (".../rest/v1"). Storage lives at
    /storage/v1 — a sibling of /rest/v1, not a child — so leaving the
    suffix on produces /rest/v1/storage/v1/... and a 404."""
    if not settings.SUPABASE_URL:
        return ""
    base = settings.SUPABASE_URL.strip().rstrip("/")
    for suffix in ("/rest/v1", "/storage/v1", "/auth/v1"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    return base.rstrip("/")


def public_url(name: str) -> str:
    """The public read url for one object. Returns "" when storage is not
    configured, so callers comparing against it as a prefix treat every
    existing value as already-current instead of churning on it."""
    base = project_base_url()
    if not base:
        return ""
    return f"{base}/storage/v1/object/public/{settings.EDITORIAL_BACKGROUND_BUCKET}/{name}"


async def _fetch_object_names() -> list[str]:
    base = project_base_url()
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            f"{base}/storage/v1/object/list/{settings.EDITORIAL_BACKGROUND_BUCKET}",
            headers={
                "Authorization": f"Bearer {settings.SUPABASE_SERVICE_KEY}",
                "apikey": settings.SUPABASE_SERVICE_KEY,
            },
            json={"prefix": "", "limit": LIST_PAGE_SIZE, "sortBy": {"column": "name", "order": "asc"}},
        )
        response.raise_for_status()
        payload = response.json()
    # A folder placeholder comes back with a null id; real objects have one.
    return sorted(
        item["name"]
        for item in payload
        if item.get("id") and str(item.get("name", "")).lower().endswith(IMAGE_SUFFIXES)
    )


async def background_object_names() -> list[str]:
    """Bucket listing, Redis-cached. Fails open to an empty list — a storage
    blip should cost the day its photo, never the quote itself."""
    if not _configured():
        return []
    redis_client = get_redis_client()
    try:
        cached = await redis_client.get(LIST_CACHE_KEY)
        if cached is not None:
            return json.loads(cached)
    except Exception as exc:  # noqa: BLE001 - cache is an optimisation, not a dependency
        logger.warning("Background list cache read failed: %s", exc)

    try:
        names = await _fetch_object_names()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Supabase background listing failed: %s", exc)
        return []
    if not names:
        logger.warning("Background bucket %s is empty", settings.EDITORIAL_BACKGROUND_BUCKET)

    try:
        await redis_client.setex(LIST_CACHE_KEY, LIST_CACHE_TTL_SECONDS, json.dumps(names))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Background list cache write failed: %s", exc)
    return names


async def pick_background(feature_date: date) -> dict | None:
    """The day's background, chosen by date ordinal so it varies day to day
    and is stable for any given date regardless of which droplet answers."""
    names = await background_object_names()
    if not names:
        return None
    return {"url": public_url(names[feature_date.toordinal() % len(names)])}