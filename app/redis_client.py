"""
Shared async Redis client — the same instance slowapi's rate limiter already
connects to (settings.REDIS_URL), reused here for two more things: (1) the
health check endpoint pinging it as a real dependency check, not just
"the process is up", and (2) short-TTL read-caching on the hot list
endpoints (see main.py's cache_response). One client, lazily created, so
none of these call sites need to know about connection pooling.

health_check_interval + socket_keepalive: the managed Valkey instance (or
the network path to it) silently drops idle connections. Without these,
the pool hands out a dead connection and the first use fails with
"Connection lost" before a fresh one is opened on retry. With them, the
pool pings idle connections and transparently replaces dead ones before
handing them out.
"""
import redis.asyncio as redis

from app.config import settings

_redis_client: "redis.Redis | None" = None


def get_redis_client() -> "redis.Redis":
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            health_check_interval=30,
            socket_keepalive=True,
            retry_on_timeout=True,
        )
    return _redis_client
