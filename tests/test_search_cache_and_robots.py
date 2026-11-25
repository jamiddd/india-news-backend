"""robots.txt and shared-cache behaviour for GET /search.

A distributed headless-Chrome crawler was walking the website's /topic/<slug>
pages (each runs a GET /search), at ~50 unique queries a minute. robots.txt keeps
polite crawlers out of /topic/, and /search responses are cached longer and made
shared-cacheable.
"""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import main
from app.database import get_db

PUBLIC = "public, max-age=60, s-maxage=600"
PRIVATE = "private, max-age=0, must-revalidate"


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeDB:
    async def execute(self, _statement):
        return _FakeResult([SimpleNamespace(
            id=1, name="Outlet", slug="outlet", feed_url="https://example.com/f.xml",
            homepage_url=None, language="en", category="national", region="india", status="active",
        )])


@pytest.fixture
def api(monkeypatch):
    store: dict = {}
    sets: list = []
    searches: list = []

    async def fake_get(key):
        return store.get(key)

    async def fake_set(key, value, ttl=300):
        store[key] = value
        sets.append((key, ttl))

    async def fake_search(_db, q, **_kwargs):
        searches.append(q)
        return []

    async def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(main, "_cache_get", fake_get)
    monkeypatch.setattr(main, "_cache_set", fake_set)
    monkeypatch.setattr(main, "search_clusters", fake_search)
    monkeypatch.setattr(main, "log_search_query", noop)
    monkeypatch.setattr(main.limiter, "enabled", False)

    async def override():
        yield _FakeDB()

    main.app.dependency_overrides[get_db] = override
    yield TestClient(main.app), sets, searches
    main.app.dependency_overrides.pop(get_db, None)


def test_robots_txt_keeps_crawlers_out_of_topic_pages(api):
    client, _sets, _searches = api
    r = client.get("/robots.txt")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert "Disallow: /topic/" in r.text
    assert "User-agent: *" in r.text


def test_robots_txt_does_not_block_the_api(api):
    """Googlebot must fetch /api/v1/* to render any page of this client-rendered
    site; disallowing it would hurt indexing of the whole site."""
    client, _sets, _searches = api
    text = client.get("/robots.txt").text
    assert "/api" not in text
    assert "Disallow: /\n" not in text and not text.rstrip().endswith("Disallow: /")


def test_robots_txt_is_publicly_cacheable(api):
    client, _sets, _searches = api
    assert client.get("/robots.txt").headers["cache-control"].startswith("public")


def test_search_is_shared_cacheable_on_a_miss_and_a_hit(api):
    client, sets, searches = api
    miss = client.get("/api/v1/search?q=green%20bonds&limit=50")
    hit = client.get("/api/v1/search?q=green%20bonds&limit=50")
    assert miss.status_code == hit.status_code == 200
    assert miss.headers["cache-control"] == hit.headers["cache-control"] == PUBLIC
    assert miss.json() == hit.json() == {"items": [], "next_cursor": None, "has_more": False}
    assert searches == ["green bonds"]  # the second call never reached the query


def test_search_results_are_cached_for_ten_minutes(api):
    client, sets, _searches = api
    client.get("/api/v1/search?q=fire%20safety")
    assert [ttl for _key, ttl in sets] == [main.SEARCH_CACHE_TTL_SECONDS]
    assert main.SEARCH_CACHE_TTL_SECONDS == 600


def test_search_304_keeps_the_public_header(api):
    client, _sets, _searches = api
    first = client.get("/api/v1/search?q=legal%20ruling")
    second = client.get("/api/v1/search?q=legal%20ruling", headers={"If-None-Match": first.headers["etag"]})
    assert second.status_code == 304
    assert second.headers["cache-control"] == PUBLIC


def test_other_endpoints_keep_the_private_default(api):
    """The middleware must not let any other response become shared-cacheable."""
    client, _sets, _searches = api
    r = client.get("/api/v1/sources")
    assert r.status_code == 200
    assert r.headers["cache-control"] == PRIVATE
