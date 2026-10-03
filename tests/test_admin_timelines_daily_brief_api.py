"""
Admin JSON APIs for Timelines (picks, search, lead-image override;
app/admin_timelines.py) and the Daily Brief (app/admin_daily_brief.py).
Narration has its own tests in test_timeline_narration.py.

What matters: reads need a session, writes need the CSRF header, a pick is
an upsert, the image override only accepts an image that really is in the
chain, and a rebuild is scheduled in the background (never twice, never
without audio configured).
"""
from datetime import date, datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.database import get_db
from app.main import app
from app.models import Article, DailyBrief, Source, StoryCluster, StoryTimelineFeature
from app.services import daily_brief
from app.services import timeline_narration as tn

USERNAME = "reviewer"
PASSWORD = "correct-horse"


@pytest.fixture(autouse=True)
def admin_credentials(monkeypatch):
    monkeypatch.setattr(settings, "POLL_ADMIN_USERNAME", USERNAME)
    monkeypatch.setattr(settings, "POLL_ADMIN_PASSWORD", PASSWORD)
    monkeypatch.setattr(settings, "POLL_SESSION_SECRET", "test-secret")


@pytest.fixture
async def client(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        for model in (Source, StoryCluster, Article, StoryTimelineFeature, DailyBrief):
            await conn.run_sync(model.__table__.create)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def no_statuses(ids):
        return {}

    invalidated: list[int] = []

    async def fake_invalidate(row_id):
        invalidated.append(row_id)

    monkeypatch.setattr(tn, "get_statuses", no_statuses)
    monkeypatch.setattr("app.admin_timelines._invalidate_timeline_caches", fake_invalidate)

    # Daily Brief build plumbing (Redis status + lease + the build itself).
    brief = {"status": {}, "running": False, "configured": True, "scheduled": []}

    async def get_status(day, kind="brief"):
        return brief["status"].get(kind, {})

    async def in_progress(kind="brief"):
        return brief["running"]

    async def set_status(day, kind, state, message=""):
        brief["status"][kind] = {"state": state, "message": message, "at": "2026-10-03T00:00:00+00:00"}

    async def run_build_task(day, kind="brief", *, force=True):
        brief["scheduled"].append((kind, force))

    monkeypatch.setattr(daily_brief, "get_status", get_status)
    monkeypatch.setattr(daily_brief, "in_progress", in_progress)
    monkeypatch.setattr(daily_brief, "set_status", set_status)
    monkeypatch.setattr(daily_brief, "run_build_task", run_build_task)
    monkeypatch.setattr("app.admin_daily_brief.is_configured", lambda: brief["configured"])

    async def override():
        async with Session() as session:
            yield session

    app.dependency_overrides[get_db] = override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        c.session_factory, c.brief, c.invalidated = Session, brief, invalidated
        yield c
    app.dependency_overrides.clear()


async def sign_in(client) -> str:
    r = await client.post("/admin/api/login", json={"username": USERNAME, "password": PASSWORD})
    assert r.status_code == 200
    return (await client.get("/admin/api/session")).json()["csrf"]


async def add(client, *objs):
    async with client.session_factory() as s:
        s.add_all(objs)
        await s.commit()
        return [o.id for o in objs]


def when(day):
    return datetime(2026, 9, day, 6, tzinfo=timezone.utc)


async def seed_chain(client):
    """Two clusters with three article images (one duplicated), plus a
    timeline row whose chain covers both."""
    source = Source(id=1, name="The Hindu", slug="hindu", feed_url="https://h/rss")
    c1 = StoryCluster(id=1, headline="Monsoon session opens")
    c2 = StoryCluster(id=2, headline="Monsoon session ends")
    await add(client, source, c1, c2)
    await add(client,
              Article(source_id=1, cluster_id=1, url="u1", url_hash="h1", title="a", published_at=when(1),
                      image_url="https://img/old.jpg", image_width=400, image_height=300),
              Article(source_id=1, cluster_id=2, url="u2", url_hash="h2", title="b", published_at=when(2),
                      image_url="https://img/hd.jpg", image_width=1600, image_height=900),
              Article(source_id=1, cluster_id=2, url="u3", url_hash="h3", title="c", published_at=when(3),
                      image_url="https://img/old.jpg", image_width=400, image_height=300))
    [row_id] = await add(client, StoryTimelineFeature(anchor_cluster_id=1, title="Monsoon session",
                                                      cluster_ids=[1, 2], coherent=True))
    return row_id


class TestTimelines:
    async def test_reads_need_a_session_and_writes_need_csrf(self, client):
        row_id = await seed_chain(client)
        for path in ("/admin/api/timelines", f"/admin/api/timelines/image/{row_id}"):
            assert (await client.get(path)).status_code == 401, path
        await sign_in(client)
        for path, body in (("/admin/api/timelines/update", {"clusterId": 2, "action": "pick"}),
                           (f"/admin/api/timelines/image/{row_id}", {"imageUrl": "https://img/hd.jpg"}),
                           (f"/admin/api/timelines/image/{row_id}/clear", {})):
            assert (await client.post(path, json=body)).status_code == 403, path

    async def test_list_shows_rows_and_search_skips_picked_clusters(self, client):
        row_id = await seed_chain(client)
        await sign_in(client)
        body = (await client.get("/admin/api/timelines", params={"q": "monsoon"})).json()
        [row] = body["rows"]
        assert row["id"] == row_id and row["label"] == "Monsoon session" and row["chainLength"] == 2
        assert row["isEditorialPick"] is False and row["clusterUrl"] == "/api/v1/clusters/1"
        assert [c["id"] for c in body["search"]["results"]] == [2]  # cluster 1 already has a row
        assert (await client.get("/admin/api/timelines")).json()["search"] is None

    async def test_pick_and_unpick(self, client):
        await seed_chain(client)
        csrf = await sign_in(client)
        h = {"X-CSRF-Token": csrf}

        r = await client.post("/admin/api/timelines/update", json={"clusterId": 2, "action": "pick"}, headers=h)
        assert r.status_code == 200
        r = await client.post("/admin/api/timelines/update", json={"clusterId": 1, "action": "pick"}, headers=h)
        assert r.status_code == 200  # existing row: upsert flips the flag
        rows = {r["anchorClusterId"]: r for r in (await client.get("/admin/api/timelines")).json()["rows"]}
        assert rows[1]["isEditorialPick"] and rows[2]["isEditorialPick"]

        r = await client.post("/admin/api/timelines/update", json={"clusterId": 1, "action": "unpick"}, headers=h)
        assert r.status_code == 200
        rows = {r["anchorClusterId"]: r for r in (await client.get("/admin/api/timelines")).json()["rows"]}
        assert rows[1]["isEditorialPick"] is False

        assert (await client.post("/admin/api/timelines/update", json={"clusterId": 99, "action": "pick"},
                                  headers=h)).status_code == 404
        assert (await client.post("/admin/api/timelines/update", json={"clusterId": 99, "action": "unpick"},
                                  headers=h)).status_code == 404
        assert (await client.post("/admin/api/timelines/update", json={"clusterId": 1, "action": "zap"},
                                  headers=h)).status_code == 422

    async def test_image_picker_lists_set_and_clear(self, client):
        row_id = await seed_chain(client)
        csrf = await sign_in(client)
        h = {"X-CSRF-Token": csrf}

        body = (await client.get(f"/admin/api/timelines/image/{row_id}")).json()
        urls = [c["imageUrl"] for c in body["candidates"]]
        assert urls == ["https://img/hd.jpg", "https://img/old.jpg"]  # deduped, HD first
        assert body["candidates"][0]["hd"] and body["manualImageUrl"] is None

        r = await client.post(f"/admin/api/timelines/image/{row_id}", json={"imageUrl": "https://evil/x.jpg"},
                              headers=h)
        assert r.status_code == 400 and client.invalidated == []

        r = await client.post(f"/admin/api/timelines/image/{row_id}", json={"imageUrl": "https://img/old.jpg"},
                              headers=h)
        assert r.status_code == 200 and client.invalidated == [row_id]
        body = (await client.get(f"/admin/api/timelines/image/{row_id}")).json()
        assert body["manualImageUrl"] == "https://img/old.jpg"
        assert [c["current"] for c in body["candidates"]] == [False, True]

        r = await client.post(f"/admin/api/timelines/image/{row_id}/clear", headers=h)
        assert r.status_code == 200 and client.invalidated == [row_id, row_id]
        assert (await client.get(f"/admin/api/timelines/image/{row_id}")).json()["manualImageUrl"] is None

        assert (await client.get("/admin/api/timelines/image/9999")).status_code == 404


def brief_row(kind="brief", day=date(2026, 10, 3), **kw):
    values = dict(brief_date=day, kind=kind, status="ready", generated_at=when(30),
                  audio_url="https://cdn/brief.m4a", audio_duration_seconds=185,
                  items=[{"cluster_id": 7, "headline": "Rates held", "summary": "RBI held rates.",
                          "category": "Business", "source_count": 9, "slot_kind": "most_covered",
                          "audio_offset": 12.0, "image_url": "https://img/r.jpg"}],
                  script={"intro": "Good morning.", "closing": "That's all.",
                          "items": [{"cluster_id": 7, "spoken": "The RBI held rates today."}]})
    values.update(kw)
    return DailyBrief(**values)


class TestDailyBrief:
    async def test_reads_need_a_session_and_writes_need_csrf(self, client):
        for path in ("/admin/api/daily-brief", "/admin/api/daily-brief/status"):
            assert (await client.get(path)).status_code == 401, path
        assert (await client.post("/admin/api/daily-brief/rebuild", json={"kind": "brief"})).status_code == 401
        await sign_in(client)
        assert (await client.post("/admin/api/daily-brief/rebuild", json={"kind": "brief"})).status_code == 403
        assert client.brief["scheduled"] == []

    async def test_overview_shows_latest_of_each_kind(self, client):
        await add(client, brief_row(day=date(2026, 9, 1)), brief_row(),
                  brief_row(kind="wrapup", audio_url=None, audio_duration_seconds=None))
        await sign_in(client)

        body = (await client.get("/admin/api/daily-brief")).json()
        assert body["kind"] == "brief" and body["kinds"]["wrapup"] == "Late-Night Wrap-up"
        b = body["brief"]
        assert b["briefDate"] == "2026-10-03" and b["audio"] == {"url": "https://cdn/brief.m4a", "durationSeconds": 185}
        assert b["intro"] == "Good morning." and b["closing"] == "That's all."
        [item] = b["items"]
        assert item["spoken"] == "The RBI held rates today." and item["summary"] == "RBI held rates."
        assert item["slotKind"] == "most_covered" and item["sourceCount"] == 9 and item["audioOffset"] == 12.0
        assert body["status"]["state"] == "idle" and body["confirm"]

        wrap = (await client.get("/admin/api/daily-brief", params={"kind": "wrapup"})).json()
        assert wrap["kind"] == "wrapup" and wrap["brief"]["audio"] is None  # text-only
        assert (await client.get("/admin/api/daily-brief", params={"kind": "bogus"})).json()["kind"] == "brief"

    async def test_overview_with_nothing_built(self, client):
        await sign_in(client)
        body = (await client.get("/admin/api/daily-brief")).json()
        assert body["brief"] is None and body["builtToday"] is False

    async def test_rebuild_schedules_in_background_and_reports_status(self, client):
        csrf = await sign_in(client)
        h = {"X-CSRF-Token": csrf}

        r = await client.post("/admin/api/daily-brief/rebuild", json={"kind": "wrapup"}, headers=h)
        assert r.status_code == 200 and r.json()["state"] == "running"
        assert client.brief["scheduled"] == [("wrapup", True)]
        status = (await client.get("/admin/api/daily-brief/status", params={"kind": "wrapup"})).json()
        assert status["state"] == "running" and status["kind"] == "wrapup"

        client.brief["running"] = True
        r = await client.post("/admin/api/daily-brief/rebuild", json={"kind": "brief"}, headers=h)
        assert r.status_code == 409 and "already running" in r.json()["detail"]

        client.brief["running"] = False
        client.brief["configured"] = False
        r = await client.post("/admin/api/daily-brief/rebuild", json={"kind": "brief"}, headers=h)
        assert r.status_code == 503 and "isn't configured" in r.json()["detail"]
        assert client.brief["scheduled"] == [("wrapup", True)]

        assert (await client.post("/admin/api/daily-brief/rebuild", json={"kind": "nope"},
                                  headers=h)).status_code == 422
