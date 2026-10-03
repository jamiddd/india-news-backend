"""
The Explainers admin JSON API (app/admin_explainers.py), driven the way the
SPA section (app/static/admin/sections/explainers.js) drives it.

What matters: nothing reads without a session, nothing writes without the CSRF
header, and the business rules the old HTML pages enforced still hold (the
trigger story leads a from-story draft's sources, generation needs at least
one source and runs in the background, one generation at a time).
Claude, Sarvam, Redis and the job lease are all monkeypatched.
"""
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import admin_explainers
from app.config import settings
from app.database import get_db
from app.main import app
from app.models import Explainer, StoryCluster
from app.services import explainer as explainer_service

USERNAME = "reviewer"
PASSWORD = "correct-horse"


@pytest.fixture(autouse=True)
def admin_credentials(monkeypatch):
    monkeypatch.setattr(settings, "POLL_ADMIN_USERNAME", USERNAME)
    monkeypatch.setattr(settings, "POLL_ADMIN_PASSWORD", PASSWORD)
    monkeypatch.setattr(settings, "POLL_SESSION_SECRET", "test-secret")


@pytest.fixture(autouse=True)
def fake_services(monkeypatch):
    """Records every service call instead of touching Redis/Postgres/Claude."""
    calls = []
    state = {"in_progress": False, "status": {}}

    async def rec(name, *args, **kwargs):
        calls.append((name, args, kwargs))

    async def in_progress(eid):
        return state["in_progress"]

    async def get_status(eid):
        return state["status"]

    async def set_status(eid, s, msg=""):
        await rec("set_status", eid, s, msg)

    async def fetch_source_clusters(ids):
        return [StoryCluster(id=i, headline=f"Story {i}", distinct_source_count=3) for i in ids]

    async def regenerate_section(eid, idx):
        await rec("regenerate_section", eid, idx)
        return state.get("section_ok", True)

    for name in ("add_source", "remove_source", "publish", "archive", "restore"):
        async def f(*a, _n=name, **k):
            await rec(_n, *a, **k)
        monkeypatch.setattr(explainer_service, name, f)

    async def run_build_task(*a, **k):
        await rec("run_build_task", *a, **k)

    async def run_regenerate_audio_task(*a, **k):
        await rec("run_regenerate_audio_task", *a, **k)

    async def trending(limit=5):
        return [("rupee", 12)]

    async def background(db, trigger, question=None):
        return [StoryCluster(id=7, headline="Old background")]

    async def suggest_category(db, trigger):
        return "business"

    async def search(db, q, limit=20, **k):
        calls.append(("search", (q, limit), {}))
        return [StoryCluster(id=1, headline="Rupee hits low", distinct_source_count=5),
                StoryCluster(id=2, headline="RBI steps in", distinct_source_count=2)]

    monkeypatch.setattr(explainer_service, "in_progress", in_progress)
    monkeypatch.setattr(explainer_service, "get_status", get_status)
    monkeypatch.setattr(explainer_service, "set_status", set_status)
    monkeypatch.setattr(explainer_service, "fetch_source_clusters", fetch_source_clusters)
    monkeypatch.setattr(explainer_service, "regenerate_section", regenerate_section)
    monkeypatch.setattr(explainer_service, "run_build_task", run_build_task)
    monkeypatch.setattr(explainer_service, "run_regenerate_audio_task", run_regenerate_audio_task)
    monkeypatch.setattr(admin_explainers, "get_trending_terms", trending)
    monkeypatch.setattr(admin_explainers, "search_clusters", search)
    monkeypatch.setattr(admin_explainers, "audio_is_configured", lambda: True)
    monkeypatch.setattr(admin_explainers.explainer_sourcing, "suggest_background_clusters", background)
    monkeypatch.setattr(admin_explainers.explainer_sourcing, "suggest_category", suggest_category)
    return {"calls": calls, "state": state}


@pytest.fixture
async def client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Explainer.__table__.create)
        await conn.run_sync(StoryCluster.__table__.create)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def override():
        async with Session() as session:
            yield session

    app.dependency_overrides[get_db] = override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        c.session_factory = Session
        yield c
    app.dependency_overrides.clear()


async def sign_in(client) -> dict:
    r = await client.post("/admin/api/login", json={"username": USERNAME, "password": PASSWORD})
    assert r.status_code == 200
    s = (await client.get("/admin/api/session")).json()
    return {"X-CSRF-Token": s["csrf"]}


async def seed(client, **kwargs) -> int:
    fields = {"question": "Why is the rupee falling?", "category": "business", "depth": "standard"}
    fields.update(kwargs)
    async with client.session_factory() as session:
        row = Explainer(**fields)
        session.add(row)
        await session.commit()
        return row.id


async def seed_cluster(client, cid=42) -> int:
    async with client.session_factory() as session:
        session.add(StoryCluster(id=cid, headline="Rupee slides past 90", summary="Importers scramble.",
                                 distinct_source_count=8))
        await session.commit()
    return cid


def names(fake):
    return [c[0] for c in fake["calls"]]


class TestAuth:
    @pytest.mark.parametrize("path", ["/admin/api/explainers", "/admin/api/explainers/options",
                                      "/admin/api/explainers/from-story?q=rupee", "/admin/api/explainers/1",
                                      "/admin/api/explainers/1/status"])
    async def test_reads_need_a_session(self, client, path):
        assert (await client.get(path)).status_code == 401

    @pytest.mark.parametrize("path,body", [
        ("", {"question": "Q"}),
        ("/1/generate", {}),
        ("/1/publish", {}),
        ("/1/archive", {}),
        ("/1/restore", {}),
        ("/1/sources/add", {"clusterId": 3}),
        ("/1/regenerate-section", {"sectionIndex": 0}),
        ("/1/regenerate-audio", {}),
        ("/from-story/1", {"question": "Q"}),
    ])
    async def test_writes_need_the_csrf_header(self, client, path, body):
        await sign_in(client)
        await seed(client)
        r = await client.post("/admin/api/explainers" + path, json=body)
        assert r.status_code == 403

    async def test_writes_without_session_are_401(self, client):
        r = await client.post("/admin/api/explainers", json={"question": "Q"})
        assert r.status_code == 401


class TestListAndCreate:
    async def test_list_counts_items_and_suggestions(self, client):
        await sign_in(client)
        await seed(client)
        await seed(client, status="ready_for_review", question="What is repo rate?")
        r = (await client.get("/admin/api/explainers")).json()
        assert r["counts"]["draft"] == 1 and r["counts"]["ready_for_review"] == 1
        assert {i["question"] for i in r["items"]} == {"Why is the rupee falling?", "What is repo rate?"}
        assert r["suggestions"] == [{"term": "rupee", "count": 12}]

    async def test_pending_count_is_ready_for_review(self, client):
        await seed(client, status="ready_for_review")
        await seed(client, status="ready_for_review")
        await seed(client, status="published")
        async with client.session_factory() as session:
            assert await admin_explainers.pending_count(session) == 2

    async def test_create_draft(self, client):
        h = await sign_in(client)
        r = await client.post("/admin/api/explainers", headers=h, json={
            "question": "  Why is the rupee falling?  ", "category": "business", "depth": "deep",
            "adminNotes": "focus on importers"})
        assert r.status_code == 200
        d = (await client.get(f"/admin/api/explainers/{r.json()['id']}")).json()
        assert (d["question"], d["category"], d["depth"], d["adminNotes"], d["status"]) == (
            "Why is the rupee falling?", "business", "deep", "focus on importers", "draft")

    async def test_blank_question_is_refused(self, client):
        h = await sign_in(client)
        r = await client.post("/admin/api/explainers", headers=h, json={"question": "   "})
        assert r.status_code == 422
        assert r.json()["detail"] == "Write the question first."

    async def test_options(self, client):
        await sign_in(client)
        r = (await client.get("/admin/api/explainers/options")).json()
        assert {d["value"] for d in r["depths"]} == {"quick", "standard", "deep"}
        assert r["audioConfigured"] is True


class TestFromStory:
    async def test_search_and_flags_attached(self, client, fake_services):
        await sign_in(client)
        eid = await seed(client, source_cluster_ids=[1])
        r = (await client.get("/admin/api/explainers/from-story", params={"q": "rupee"})).json()
        assert [i["id"] for i in r["items"]] == [1, 2]
        assert "attached" not in r["items"][0]
        r = (await client.get("/admin/api/explainers/from-story",
                              params={"q": "rupee", "limit": 8, "explainer_id": eid})).json()
        assert [i["attached"] for i in r["items"]] == [True, False]
        assert ("search", ("rupee", 8), {}) in fake_services["calls"]

    async def test_empty_search_returns_nothing(self, client):
        await sign_in(client)
        assert (await client.get("/admin/api/explainers/from-story")).json()["items"] == []

    async def test_story_detail_suggests_category(self, client):
        await sign_in(client)
        cid = await seed_cluster(client)
        r = (await client.get(f"/admin/api/explainers/from-story/{cid}")).json()
        assert r["story"]["headline"] == "Rupee slides past 90"
        assert r["suggestedCategory"] == "business"
        assert (await client.get("/admin/api/explainers/from-story/999")).status_code == 404

    async def test_create_from_story_puts_trigger_first(self, client):
        h = await sign_in(client)
        cid = await seed_cluster(client)
        r = await client.post(f"/admin/api/explainers/from-story/{cid}", headers=h,
                              json={"question": "Why now?", "category": "business"})
        assert r.status_code == 200 and r.json()["sourceCount"] == 2
        d = (await client.get(f"/admin/api/explainers/{r.json()['id']}")).json()
        assert d["sourceClusterIds"] == [cid, 7]
        assert [s["id"] for s in d["attachedSources"]] == [cid, 7]


class TestReview:
    async def test_missing_explainer_is_404(self, client):
        await sign_in(client)
        r = await client.get("/admin/api/explainers/999")
        assert r.status_code == 404
        assert r.json()["detail"] == "That explainer doesn't exist."

    async def test_ready_detail_has_content(self, client):
        await sign_in(client)
        eid = await seed(client, status="ready_for_review", quick_answer="Because.",
                         sections=[{"heading": "Why", "body": "Reasons."}],
                         sources=[{"cluster_id": 3, "title": "T", "outlet": "O", "url": "u", "source_count": 4}],
                         audio_url="https://cdn/x.mp3", audio_duration_seconds=125, voice="simran",
                         hero_image_url="https://cdn/h.jpg")
        d = (await client.get(f"/admin/api/explainers/{eid}")).json()
        assert d["quickAnswer"] == "Because."
        assert d["sections"] == [{"heading": "Why", "body": "Reasons."}]
        assert d["sources"][0]["clusterId"] == 3
        assert (d["audioUrl"], d["audioDurationSeconds"], d["voice"]) == ("https://cdn/x.mp3", 125, "simran")
        assert d["heroImageUrl"] == "https://cdn/h.jpg"
        assert d["running"] is False

    async def test_add_and_remove_source(self, client, fake_services):
        h = await sign_in(client)
        eid = await seed(client)
        assert (await client.post(f"/admin/api/explainers/{eid}/sources/add", headers=h, json={"clusterId": 5})).status_code == 200
        assert (await client.post(f"/admin/api/explainers/{eid}/sources/remove", headers=h, json={"clusterId": 5})).status_code == 200
        assert [(n, a) for n, a, _ in fake_services["calls"]] == [("add_source", (eid, 5)), ("remove_source", (eid, 5))]

    async def test_generate_needs_a_source(self, client, fake_services):
        h = await sign_in(client)
        eid = await seed(client)
        r = await client.post(f"/admin/api/explainers/{eid}/generate", headers=h, json={})
        assert r.status_code == 422
        assert "run_build_task" not in names(fake_services)

    async def test_generate_runs_in_background(self, client, fake_services):
        h = await sign_in(client)
        eid = await seed(client, source_cluster_ids=[1])
        r = await client.post(f"/admin/api/explainers/{eid}/generate", headers=h, json={"narrate": True, "voice": "simran"})
        assert r.status_code == 200
        assert ("set_status", (eid, "generating", "starting"), {}) in fake_services["calls"]
        assert ("run_build_task", (eid,), {"narrate": True, "voice": "simran"}) in fake_services["calls"]

    async def test_generate_refused_while_running(self, client, fake_services):
        h = await sign_in(client)
        eid = await seed(client, source_cluster_ids=[1])
        fake_services["state"]["in_progress"] = True
        r = await client.post(f"/admin/api/explainers/{eid}/generate", headers=h, json={})
        assert r.status_code == 409
        r = await client.post(f"/admin/api/explainers/{eid}/regenerate-audio", headers=h, json={})
        assert r.status_code == 409

    async def test_status_reports_generating(self, client, fake_services):
        await sign_in(client)
        fake_services["state"]["status"] = {"state": "generating", "message": "voicing"}
        r = (await client.get("/admin/api/explainers/3/status")).json()
        assert r == {"explainerId": 3, "state": "generating", "message": "voicing"}
        fake_services["state"]["status"] = {}
        assert (await client.get("/admin/api/explainers/3/status")).json()["state"] == "idle"

    async def test_regenerate_audio(self, client, fake_services):
        h = await sign_in(client)
        eid = await seed(client, status="published", quick_answer="A", sections=[{"heading": "H", "body": "B"}])
        r = await client.post(f"/admin/api/explainers/{eid}/regenerate-audio", headers=h, json={"voice": "shubh"})
        assert r.status_code == 200
        assert ("run_regenerate_audio_task", (eid,), {"voice": "shubh"}) in fake_services["calls"]

    async def test_regenerate_section(self, client, fake_services):
        h = await sign_in(client)
        eid = await seed(client, status="ready_for_review", sections=[{"heading": "H", "body": "B"}])
        assert (await client.post(f"/admin/api/explainers/{eid}/regenerate-section", headers=h,
                                  json={"sectionIndex": 0})).status_code == 200
        assert (await client.post(f"/admin/api/explainers/{eid}/regenerate-section", headers=h,
                                  json={"sectionIndex": 3})).status_code == 422
        fake_services["state"]["section_ok"] = False
        r = await client.post(f"/admin/api/explainers/{eid}/regenerate-section", headers=h, json={"sectionIndex": 0})
        assert r.status_code == 502

    async def test_publish_archive_restore(self, client, fake_services):
        h = await sign_in(client)
        draft = await seed(client)
        assert (await client.post(f"/admin/api/explainers/{draft}/publish", headers=h)).status_code == 409
        ready = await seed(client, status="ready_for_review")
        assert (await client.post(f"/admin/api/explainers/{ready}/publish", headers=h)).status_code == 200
        assert (await client.post(f"/admin/api/explainers/{ready}/archive", headers=h)).status_code == 200
        archived = await seed(client, status="archived")
        assert (await client.post(f"/admin/api/explainers/{archived}/restore", headers=h)).status_code == 200
        assert (await client.post(f"/admin/api/explainers/{ready}/restore", headers=h)).status_code == 409
        assert [(n, a) for n, a, _ in fake_services["calls"]] == [
            ("publish", (ready,)), ("archive", (ready,)), ("restore", (archived,))]
