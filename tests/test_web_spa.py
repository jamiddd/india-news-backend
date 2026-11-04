"""
The public web app shell (app/web_spa.py): share pages inject meta into the SPA
index.html, the catch-all serves it for SPA-only paths, and nothing the server
owns gets swallowed. Uses a throwaway dist dir and in-memory SQLite.
"""
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import web_spa
from app.database import get_db
from app.main import app, limiter
from app.models import Article, StoryCluster

INDEX = (
    "<!DOCTYPE html><html><head><title>Open Indian News</title>"
    '<script type="module" src="/app-assets/assets/index-x.js"></script>'
    '</head><body><div id="root"></div></body></html>'
)


@pytest.fixture(autouse=True)
def no_rate_limit(monkeypatch):
    monkeypatch.setattr(limiter, "enabled", False)


@pytest.fixture
def dist(tmp_path, monkeypatch):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text(INDEX)
    monkeypatch.setattr(web_spa, "DIST_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def no_dist(tmp_path, monkeypatch):
    monkeypatch.setattr(web_spa, "DIST_DIR", tmp_path / "missing")


@pytest.fixture
async def client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(StoryCluster.__table__.create)
        await conn.run_sync(Article.__table__.create)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def override():
        async with Session() as session:
            yield session

    app.dependency_overrides[get_db] = override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        c.session_factory = Session
        yield c
    app.dependency_overrides.clear()


def test_inject_meta_replaces_title_and_escapes():
    out = web_spa.inject_meta(INDEX, title='A "big" <story>', description="d & e",
                              url="https://x/y", image="https://i/1.jpg", og_type="article")
    assert out.count("<title>") == 1
    assert "<title>A &quot;big&quot; &lt;story&gt; — Open Indian News</title>" in out
    assert 'property="og:description" content="d &amp; e"' in out
    assert 'property="og:image" content="https://i/1.jpg"' in out
    assert out.index("og:title") < out.index("</head>")
    assert '<script type="module"' in out


def test_inject_meta_without_title_tag_adds_one():
    out = web_spa.inject_meta("<html><head></head></html>", title="About")
    assert "<title>About — Open Indian News</title>" in out


def test_is_reserved():
    for p in ["api/v1/x", "admin", "admin/polls", "health", "status", "static/a.css",
              ".well-known/assetlinks.json", "download/apk", "app-assets/assets/a.js"]:
        assert web_spa.is_reserved(p), p
    for p in ["saved", "settings", "source/3", "topic/sports", "downloads", "healthy"]:
        assert not web_spa.is_reserved(p), p


@pytest.mark.parametrize("path", ["/saved", "/settings", "/profile", "/watch", "/sources",
                                  "/source/3", "/topic/sports", "/timeline/9", "/explainer/2"])
async def test_spa_paths_get_shell(client, dist, path):
    r = await client.get(path)
    assert r.status_code == 200
    assert "/app-assets/assets/index-x.js" in r.text
    assert "no-cache" in r.headers["cache-control"]


@pytest.mark.parametrize("path", ["/api/v1/nope", "/admin/api/nope", "/static/nope.css",
                                  "/.well-known/nope", "/download/apk/x", "/app-assets/assets/nope.js"])
async def test_server_paths_not_swallowed(client, dist, path):
    r = await client.get(path)
    assert r.status_code == 404
    assert "app-assets" not in r.text


@pytest.mark.parametrize("path,content_type", [("/favicon.ico", "image/x-icon"),
                                                ("/manifest.webmanifest", "application/manifest+json"),
                                                ("/sw.js", "application/javascript")])
async def test_root_files_are_real_files_not_the_shell(client, dist, path, content_type):
    """The icon, the install manifest and the service worker live at the site root (their scope and
    browsers' own requests need that) and must not fall through to the app shell."""
    r = await client.get(path)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(content_type)
    assert "app-assets" not in r.text[:400] if path != "/favicon.ico" else True


async def test_service_worker_is_never_cached_and_covers_the_whole_site(client, dist):
    r = await client.get("/sw.js")
    assert "no-cache" in r.headers["cache-control"]        # a new worker must be picked up at once
    assert r.headers["service-worker-allowed"] == "/"


async def test_registered_routes_still_win(client, dist):
    r = await client.get("/health")
    assert "app-assets" not in r.text
    r = await client.get("/.well-known/assetlinks.json")
    assert r.status_code == 200 and r.json()[0]["target"]["package_name"] == "com.jamid.news"


async def test_assets_served_when_built(dist):
    (dist / "assets" / "a.js").write_text("1")
    mount = web_spa.make_assets(dist)
    assert mount is not None
    assert web_spa.make_assets(dist / "nope") is None


async def test_catchall_404s_without_dist(client, no_dist):
    assert (await client.get("/saved")).status_code == 404


async def test_story_share_page_injects_meta_into_shell(client, dist):
    async with client.session_factory() as s:
        s.add(StoryCluster(id=7, headline="Big <news> today", summary="• one\n• two"))
        await s.commit()
    r = await client.get("/story/7")
    assert r.status_code == 200
    assert "/app-assets/assets/index-x.js" in r.text
    assert "<title>Big &lt;news&gt; today — Open Indian News</title>" in r.text
    assert 'property="og:url" content="https://openindiannews.com/story/7"' in r.text


async def test_story_falls_back_to_legacy_html_without_dist(client, no_dist):
    async with client.session_factory() as s:
        s.add(StoryCluster(id=8, headline="Legacy", summary="x"))
        await s.commit()
    r = await client.get("/story/8")
    assert r.status_code == 200
    assert "app-assets" not in r.text
    assert "/static/site.css" in r.text


async def test_story_not_found_is_404_shell(client, dist):
    r = await client.get("/story/999")
    assert r.status_code == 404
    assert "/app-assets/assets/index-x.js" in r.text
    assert "Story not found" in r.text


async def test_static_page_renders_into_shell(client, dist):
    r = await client.get("/about")
    assert r.status_code == 200
    assert "<title>About — Open Indian News</title>" in r.text
    assert "/app-assets/assets/index-x.js" in r.text


async def test_static_page_legacy_without_dist(client, no_dist):
    r = await client.get("/about")
    assert r.status_code == 200
    assert "app-assets" not in r.text
