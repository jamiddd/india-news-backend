from __future__ import annotations

from datetime import date

import pytest

from app.config import settings
from app.services import editorial_backgrounds as bg


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(settings, "SUPABASE_URL", "https://proj.supabase.co", raising=False)
    monkeypatch.setattr(settings, "SUPABASE_SERVICE_KEY", "service-key", raising=False)
    monkeypatch.setattr(settings, "EDITORIAL_BACKGROUND_BUCKET", "editorial-backgrounds", raising=False)


class _FakeRedis:
    """Enough of the async Redis surface for the list cache, so the test can
    assert the bucket is listed once and then served from cache."""

    def __init__(self):
        self.store: dict[str, str] = {}

    async def get(self, key):
        return self.store.get(key)

    async def setex(self, key, ttl, value):
        self.store[key] = value


def _stub_listing(monkeypatch, names, calls):
    async def fake_fetch():
        calls.append(1)
        return names

    monkeypatch.setattr(bg, "_fetch_object_names", fake_fetch)


@pytest.mark.asyncio
async def test_background_is_stable_per_date_and_rotates(monkeypatch):
    monkeypatch.setattr(bg, "get_redis_client", lambda: _FakeRedis())
    _stub_listing(monkeypatch, ["a.jpg", "b.jpg", "c.jpg"], [])

    first = await bg.pick_background(date(2026, 9, 4))
    again = await bg.pick_background(date(2026, 9, 4))
    next_day = await bg.pick_background(date(2026, 9, 5))

    assert first == again
    assert first != next_day
    assert first["url"].startswith("https://proj.supabase.co/storage/v1/object/public/editorial-backgrounds/")


@pytest.mark.asyncio
async def test_bucket_is_listed_once_then_cached(monkeypatch):
    redis_client = _FakeRedis()
    calls: list[int] = []
    monkeypatch.setattr(bg, "get_redis_client", lambda: redis_client)
    _stub_listing(monkeypatch, ["a.jpg"], calls)

    for _ in range(5):
        await bg.pick_background(date(2026, 9, 4))

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_listing_failure_falls_back_to_no_image(monkeypatch):
    monkeypatch.setattr(bg, "get_redis_client", lambda: _FakeRedis())

    async def boom():
        raise RuntimeError("storage down")

    monkeypatch.setattr(bg, "_fetch_object_names", boom)
    assert await bg.pick_background(date(2026, 9, 4)) is None


@pytest.mark.asyncio
async def test_unconfigured_makes_no_outbound_call(monkeypatch):
    monkeypatch.setattr(settings, "SUPABASE_SERVICE_KEY", None, raising=False)
    calls: list[int] = []
    _stub_listing(monkeypatch, ["a.jpg"], calls)

    assert await bg.pick_background(date(2026, 9, 4)) is None
    assert calls == []
    assert bg.public_url("a.jpg").startswith("https://proj.supabase.co")
