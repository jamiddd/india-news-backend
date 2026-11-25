"""GET /api/v1/sources is cached.

It returned the full source list (~45 KB from the database) on every call and
was hit ~45 times a minute -- ~3 GB/day of database egress. The cache fails open
like the other endpoint caches: a Redis error means "query the database", never a
failed request.
"""
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import main
from app.database import get_db


def _source(i: int):
    return SimpleNamespace(
        id=i, name=f"Outlet {i}", slug=f"outlet-{i}", feed_url=f"https://example.com/{i}.xml",
        homepage_url=None, language="en", category="national", region="india", status="active",
    )


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeDB:
    def __init__(self):
        self.queries = 0

    async def execute(self, _statement):
        self.queries += 1
        return _FakeResult([_source(1), _source(2)])


@pytest.fixture
def api(monkeypatch):
    db = _FakeDB()
    store: dict = {}
    sets: list = []

    async def fake_get(key):
        return store.get(key)

    async def fake_set(key, value, ttl=300):
        store[key] = value
        sets.append((key, ttl))

    monkeypatch.setattr(main, "_cache_get", fake_get)
    monkeypatch.setattr(main, "_cache_set", fake_set)
    # The rate limiter keeps its counters in Redis; this test is about the route
    # and its cache, not about throttling.
    monkeypatch.setattr(main.limiter, "enabled", False)

    async def override():
        yield db

    main.app.dependency_overrides[get_db] = override
    client = TestClient(main.app)
    yield client, db, store, sets
    main.app.dependency_overrides.pop(get_db, None)


def test_first_call_queries_the_database_and_fills_the_cache(api):
    client, db, store, sets = api
    r = client.get("/api/v1/sources")
    assert r.status_code == 200
    body = r.json()
    assert [s["id"] for s in body] == [1, 2]
    assert set(body[0]) == {
        "id", "name", "slug", "feed_url", "homepage_url", "language", "category", "region", "status",
    }
    assert db.queries == 1
    assert sets == [(main.SOURCES_CACHE_KEY, main.SOURCES_CACHE_TTL_SECONDS)]


def test_second_call_is_served_from_the_cache_without_touching_the_database(api):
    client, db, _store, _sets = api
    first = client.get("/api/v1/sources")
    second = client.get("/api/v1/sources")
    assert second.status_code == 200
    assert second.json() == first.json()
    assert db.queries == 1


def test_a_cache_failure_fails_open_to_the_database(api, monkeypatch):
    client, db, _store, _sets = api

    async def broken_get(_key):
        return None  # what _cache_get returns on any Redis error

    async def broken_set(*_args, **_kwargs):
        return None  # what _cache_set does on any Redis error

    monkeypatch.setattr(main, "_cache_get", broken_get)
    monkeypatch.setattr(main, "_cache_set", broken_set)
    r1 = client.get("/api/v1/sources")
    r2 = client.get("/api/v1/sources")
    assert r1.status_code == r2.status_code == 200
    assert json.loads(r1.text)[0]["slug"] == "outlet-1"
    assert db.queries == 2


def test_cache_ttl_is_short_enough_for_admin_edits_to_show_up():
    assert main.SOURCES_CACHE_TTL_SECONDS <= 600
