"""
Shared async Redis client — the same instance slowapi's rate limiter already
connects to (settings.REDIS_URL), reused here for two more things: (1) the
health check endpoint pinging it as a real dependency check, not just
"the process is up", and (2) short-TTL read-caching on the hot list
endpoints (see main.py's cache_response). One client, lazily created, so
none of these call sites need to know about connection pooling.
"""
import redis.asyncio as redis

from app.config import settings

_redis_client: "redis.Redis | None" = None


def get_redis_client() -> "redis.Redis":
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis_client
