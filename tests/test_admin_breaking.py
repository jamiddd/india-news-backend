"""
The admin SPA's Breaking review API (app/admin_breaking.py).

What matters: nothing reads without a session or writes without the CSRF
header; a reject never calls the LLM; an approve runs the narrative pass and
makes the story live (or rejects it when the model finds nothing citable); a
refresh approve appends beats and a reject only advances the reviewed source
count; the image picker only accepts images carried by the story's own
articles and invalidates the app's caches.
"""
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import admin_breaking
from app.config import settings
from app.database import get_db
from app.main import app
from app.models import Article, BreakingRefreshReview, BreakingStory, Source, StoryCluster

USERNAME = "reviewer"
PASSWORD = "correct-horse"
T0 = datetime(2026, 10, 3, 6, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def admin_credentials(monkeypatch):
    monkeypatch.setattr(settings, "POLL_ADMIN_USERNAME", USERNAME)
    monkeypatch.setattr(settings, "POLL_ADMIN_PASSWORD", PASSWORD)
    monkeypatch.setattr(settings, "POLL_SESSION_SECRET", "test-secret")


class FakeRedis:
    def __init__(self):
        self.deleted = []

    async def delete(self, key):
        self.deleted.append(key)


@pytest.fixture
def redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(admin_breaking, "get_redis_client", lambda: fake)
    return fake


@pytest.fixture
def llm(monkeypatch):
    """Records extract_beats_only calls; set .result / .error per test."""
    import app.services.breaking_narrative as narrative

    class Fake:
        calls = []
        result = {"title": "Floods spread", "beats": [{"time_label": "06:00", "label": "Rain", "narration": "It rained."}]}
        error = None

    async def fake(articles, existing_beats=None):
        Fake.calls.append((articles, existing_beats))
        if Fake.error:
            raise narrative.BreakingNarrativeError(Fake.error)
        return Fake.result

    Fake.calls = []
    monkeypatch.setattr(narrative, "extract_beats_only", fake)
    return Fake


@pytest.fixture
async def client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        for model in (Source, StoryCluster, Article, BreakingStory, BreakingRefreshReview):
            await conn.run_sync(model.__table__.create)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def override():
        async with Session() as session:
            yield session

    app.dependency_overrides[get_db] = override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        c.session_factory = Session
        yield c
    app.dependency_overrides.clear()


async def sign_in(client) -> str:
    r = await client.post("/admin/api/login", json={"username": USERNAME, "password": PASSWORD})
    assert r.status_code == 200
    return (await client.get("/admin/api/session")).json()["csrf"]


async def seed_cluster(client, cluster_id=1, articles=3, images=None):
    """A cluster with `articles` articles from distinct sources, one hour apart.
    `images` optionally maps article index -> (url, width, height)."""
    images = images or {}
    async with client.session_factory() as s:
        s.add(StoryCluster(id=cluster_id, headline=f"Headline {cluster_id}"))
        for i in range(articles):
            src = Source(name=f"Source {cluster_id}-{i}", slug=f"s{cluster_id}-{i}", feed_url=f"https://f/{cluster_id}/{i}")
            s.add(src)
            await s.flush()
            url, w, h = images.get(i, (None, None, None))
            s.add(Article(source_id=src.id, cluster_id=cluster_id, url=f"https://a/{cluster_id}/{i}",
                          url_hash=f"h{cluster_id}-{i}", title=f"Article {i}",
                          published_at=T0 + timedelta(hours=i), image_url=url, image_width=w, image_height=h))
        await s.commit()


async def seed_story(client, cluster_id=1, **fields):
    values = dict(cluster_id=cluster_id, status="pending_review", sources_at_promotion=3, hours_to_threshold=1.5)
    values.update(fields)
    async with client.session_factory() as s:
        s.add(BreakingStory(**values))
        await s.commit()


async def get_story(client, cluster_id=1) -> BreakingStory:
    async with client.session_factory() as s:
        from sqlalchemy import select
        return await s.scalar(select(BreakingStory).where(BreakingStory.cluster_id == cluster_id))


class TestAuth:
    async def test_reads_need_a_session(self, client):
        assert (await client.get("/admin/api/breaking")).status_code == 401
        assert (await client.get("/admin/api/breaking/image/1")).status_code == 401

    async def test_writes_need_the_csrf_header(self, client, llm):
        await seed_cluster(client)
        await seed_story(client)
        await sign_in(client)
        r = await client.post("/admin/api/breaking/candidates/1", json={"action": "reject"})
        assert r.status_code == 403
        assert (await get_story(client)).status == "pending_review"
        assert (await client.delete("/admin/api/breaking/image/1")).status_code == 403

    async def test_old_html_routes_are_gone(self, client):
        csrf = await sign_in(client)
        r = await client.post("/admin/breaking/candidate/1/decide", data={"action": "reject", "csrf": csrf})
        assert r.status_code in (404, 405)


class TestQueue:
    async def test_dashboard_shows_candidate_evidence_and_badge(self, client):
        await seed_cluster(client, 1, articles=3)
        await seed_story(client, 1)
        await seed_cluster(client, 2, articles=2)
        await seed_story(client, 2, status="active", title="Live title", beats=[{"label": "x"}], last_generated_at=T0)
        await seed_cluster(client, 3, articles=4)
        await seed_story(client, 3, status="active", beats=[{"time_label": "06:00", "label": "Start", "narration": "N"}],
                         last_generated_at=T0 + timedelta(hours=1, minutes=30), last_reviewed_source_count=3)
        async with client.session_factory() as s:
            s.add(BreakingRefreshReview(cluster_id=3, source_count_at_review=8))
            await s.commit()
        await sign_in(client)

        d = (await client.get("/admin/api/breaking")).json()
        [c] = d["candidates"]
        assert c["clusterId"] == 1 and c["headline"] == "Headline 1"
        assert c["sourcesAtPromotion"] == 3 and c["hoursToThreshold"] == 1.5
        assert [a["title"] for a in c["articles"]] == ["Article 0", "Article 1", "Article 2"]
        assert c["sources"] == ["Source 1-0", "Source 1-1", "Source 1-2"]
        assert c["omitted"] == 0
        [r] = d["refreshes"]
        assert r["sourceCount"] == 8 and r["previousSourceCount"] == 3
        assert r["beats"] == [{"timeLabel": "06:00", "label": "Start", "narration": "N"}]
        assert [a["title"] for a in r["articles"]] == ["Article 2", "Article 3"]  # only since last generation
        assert {s["clusterId"] for s in d["live"]} == {2, 3}

        # The sidebar badge (admin_api.overview) counts the candidate + the refresh.
        async with client.session_factory() as s:
            assert await admin_breaking.pending_count(s) == 2

    async def test_preview_is_capped(self, client):
        await seed_cluster(client, 1, articles=admin_breaking.PREVIEW_LIMIT + 4)
        await seed_story(client, 1)
        await sign_in(client)
        [c] = (await client.get("/admin/api/breaking")).json()["candidates"]
        assert len(c["articles"]) == admin_breaking.PREVIEW_LIMIT
        assert c["omitted"] == 4 and c["articleCount"] == admin_breaking.PREVIEW_LIMIT + 4


class TestCandidateDecisions:
    async def test_reject_never_calls_the_llm(self, client, llm):
        await seed_cluster(client)
        await seed_story(client)
        csrf = await sign_in(client)
        r = await client.post("/admin/api/breaking/candidates/1", json={"action": "reject"},
                              headers={"X-CSRF-Token": csrf})
        assert r.status_code == 200 and r.json()["status"] == "rejected"
        assert llm.calls == []
        story = await get_story(client)
        assert story.status == "rejected" and story.reviewed_at is not None

    async def test_approve_writes_the_narrative_and_goes_live(self, client, llm):
        await seed_cluster(client)
        await seed_story(client)
        csrf = await sign_in(client)
        r = await client.post("/admin/api/breaking/candidates/1", json={"action": "approve"},
                              headers={"X-CSRF-Token": csrf})
        assert r.status_code == 200
        assert r.json() == {"ok": True, "clusterId": 1, "status": "active", "title": "Floods spread", "beatCount": 1}
        assert len(llm.calls[0][0]) == 3 and llm.calls[0][1] is None
        story = await get_story(client)
        assert story.status == "active" and story.title == "Floods spread"
        assert story.last_generated_source_count == 3 and story.last_reviewed_source_count == 3
        assert story.last_beat_at.replace(tzinfo=timezone.utc) == T0 + timedelta(hours=2)

    async def test_approve_with_no_beats_rejects(self, client, llm):
        llm.result = {"title": "x", "beats": []}
        await seed_cluster(client)
        await seed_story(client)
        csrf = await sign_in(client)
        r = await client.post("/admin/api/breaking/candidates/1", json={"action": "approve"},
                              headers={"X-CSRF-Token": csrf})
        assert r.json()["status"] == "rejected"
        assert (await get_story(client)).status == "rejected"

    async def test_llm_failure_changes_nothing(self, client, llm):
        llm.error = "timeout"
        await seed_cluster(client)
        await seed_story(client)
        csrf = await sign_in(client)
        r = await client.post("/admin/api/breaking/candidates/1", json={"action": "approve"},
                              headers={"X-CSRF-Token": csrf})
        assert r.status_code == 502 and "timeout" in r.json()["detail"]
        assert (await get_story(client)).status == "pending_review"

    async def test_unknown_candidate_and_bad_action(self, client, llm):
        csrf = await sign_in(client)
        h = {"X-CSRF-Token": csrf}
        assert (await client.post("/admin/api/breaking/candidates/9", json={"action": "reject"}, headers=h)).status_code == 404
        assert (await client.post("/admin/api/breaking/candidates/9", json={"action": "maybe"}, headers=h)).status_code == 422


class TestRefreshDecisions:
    async def seed(self, client):
        await seed_cluster(client, 1, articles=4)
        await seed_story(client, 1, status="active", title="T", beats=[{"label": "old"}],
                         last_generated_at=T0 + timedelta(hours=1, minutes=30), last_reviewed_source_count=3)
        async with client.session_factory() as s:
            review = BreakingRefreshReview(cluster_id=1, source_count_at_review=8)
            s.add(review)
            await s.commit()
            return review.id

    async def test_reject_advances_reviewed_count_only(self, client, llm):
        rid = await self.seed(client)
        csrf = await sign_in(client)
        r = await client.post(f"/admin/api/breaking/refreshes/{rid}", json={"action": "reject"},
                              headers={"X-CSRF-Token": csrf})
        assert r.json()["status"] == "rejected" and llm.calls == []
        story = await get_story(client)
        assert story.last_reviewed_source_count == 8 and story.beats == [{"label": "old"}]

    async def test_approve_appends_beats(self, client, llm):
        rid = await self.seed(client)
        csrf = await sign_in(client)
        r = await client.post(f"/admin/api/breaking/refreshes/{rid}", json={"action": "approve"},
                              headers={"X-CSRF-Token": csrf})
        assert r.json() == {"ok": True, "id": rid, "status": "approved", "newBeats": 1}
        articles, existing = llm.calls[0]
        assert [a["title"] for a in articles] == ["Article 2", "Article 3"] and existing == [{"label": "old"}]
        story = await get_story(client)
        assert len(story.beats) == 2 and story.last_generated_source_count == 8
        # Already decided: a second decision is refused.
        again = await client.post(f"/admin/api/breaking/refreshes/{rid}", json={"action": "approve"},
                                  headers={"X-CSRF-Token": csrf})
        assert again.status_code == 404


class TestImagePicker:
    async def seed(self, client):
        await seed_cluster(client, 1, articles=3, images={
            0: ("https://img/small.jpg", 400, 300),
            1: ("https://img/hd.jpg", 1920, 1080),
            2: ("https://img/hd.jpg", 1920, 1080),  # duplicate URL
        })
        await seed_story(client, 1, status="active")

    async def test_lists_distinct_images_hd_first(self, client):
        await self.seed(client)
        await sign_in(client)
        d = (await client.get("/admin/api/breaking/image/1")).json()
        assert [i["url"] for i in d["images"]] == ["https://img/hd.jpg", "https://img/small.jpg"]
        assert d["images"][0]["hd"] is True and d["manualImageUrl"] is None
        assert (await client.get("/admin/api/breaking/image/9")).status_code == 404

    async def test_set_and_clear(self, client, redis):
        await self.seed(client)
        csrf = await sign_in(client)
        h = {"X-CSRF-Token": csrf}
        bad = await client.post("/admin/api/breaking/image/1", json={"imageUrl": "https://evil/x.jpg"}, headers=h)
        assert bad.status_code == 400
        r = await client.post("/admin/api/breaking/image/1", json={"imageUrl": "https://img/small.jpg"}, headers=h)
        assert r.status_code == 200
        assert (await get_story(client)).manual_image_url == "https://img/small.jpg"
        assert redis.deleted == ["breaking:list", "breaking:1"]
        d = (await client.get("/admin/api/breaking/image/1")).json()
        assert [i["url"] for i in d["images"] if i["current"]] == ["https://img/small.jpg"]
        r = await client.delete("/admin/api/breaking/image/1", headers=h)
        assert r.status_code == 200
        assert (await get_story(client)).manual_image_url is None
