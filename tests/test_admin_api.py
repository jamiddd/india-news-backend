"""
The admin SPA's core API (app/admin_api.py), its shell (app/admin_spa.py) and
the Story reports section API (app/story_reports_admin.py), which the other
section tests follow as a pattern.

What matters: nothing under /admin/api reads without a session, nothing writes
without the CSRF header, and every old admin URL still lands on the SPA shell.
"""
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.database import get_db
from app.main import app
from datetime import timedelta

from app.models import (DailyPoll, DailyQuiz, Donation, Explainer, Feedback, PremiumTrial, ReadEvent,
                        SavedStory, StoryReport, User, utc_now)

USERNAME = "reviewer"
PASSWORD = "correct-horse"


@pytest.fixture(autouse=True)
def admin_credentials(monkeypatch):
    monkeypatch.setattr(settings, "POLL_ADMIN_USERNAME", USERNAME)
    monkeypatch.setattr(settings, "POLL_ADMIN_PASSWORD", PASSWORD)
    monkeypatch.setattr(settings, "POLL_SESSION_SECRET", "test-secret")


@pytest.fixture
async def client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        for model in (Feedback, StoryReport, DailyPoll, DailyQuiz, User, Explainer, Donation, SavedStory,
                      PremiumTrial, ReadEvent):
            await conn.run_sync(model.__table__.create)
        # StoryReport/search join story_clusters; only the columns they touch matter.
        await conn.exec_driver_sql(
            "CREATE TABLE story_clusters (id INTEGER PRIMARY KEY, headline TEXT, "
            "distinct_source_count INTEGER, last_updated_at TIMESTAMP)")
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
    s = (await client.get("/admin/api/session")).json()
    assert s["signedIn"] is True
    return s["csrf"]


async def seed_report(client, **kwargs) -> int:
    fields = {"user_id": "u1", "reason": "misleading", "note": "Headline says the opposite."}
    fields.update(kwargs)
    async with client.session_factory() as session:
        item = StoryReport(**fields)
        session.add(item)
        await session.commit()
        return item.id


class TestSession:
    async def test_session_probe_never_401s(self, client):
        r = await client.get("/admin/api/session")
        assert r.status_code == 200
        assert r.json() == {"signedIn": False, "csrf": None, "username": None}

    async def test_wrong_password_is_rejected(self, client):
        r = await client.post("/admin/api/login", json={"username": USERNAME, "password": "nope"})
        assert r.status_code == 401
        assert "oin_admin" not in r.cookies

    async def test_reads_need_a_session(self, client):
        for path in ("/admin/api/overview", "/admin/api/search?q=ab", "/admin/api/reports"):
            assert (await client.get(path)).status_code == 401, path

    async def test_logout_ends_the_session(self, client):
        await sign_in(client)
        await client.post("/admin/api/logout")
        assert (await client.get("/admin/api/session")).json()["signedIn"] is False


class TestShell:
    @pytest.mark.parametrize("path", ["/admin", "/admin/", "/admin/polls", "/admin/explainers/12/review",
                                      "/admin/quiz-bank", "/admin/login", "/admin/feedback/login"])
    async def test_old_admin_urls_serve_the_spa(self, client, path):
        r = await client.get(path)
        assert r.status_code == 200
        assert 'id="app"' in r.text
        assert 'window.ADMIN_BASE = "/admin"' in r.text
        assert "__V__" not in r.text

    async def test_admin_subdomain_gets_an_empty_base(self, client):
        r = await client.get("/admin/polls", headers={"host": "admin.openindiannews.com"})
        assert 'window.ADMIN_BASE = ""' in r.text
        assert 'src="/assets/core.js' in r.text

    async def test_unknown_api_path_is_a_json_404(self, client):
        r = await client.get("/admin/api/nope")
        assert r.status_code == 404
        assert r.headers["content-type"].startswith("application/json")

    async def test_assets_are_served(self, client):
        r = await client.get("/admin/assets/core.js")
        assert r.status_code == 200
        assert "Admin.page" in r.text

    def test_spa_catch_all_is_registered_last(self):
        # Anything under /admin registered after the catch-all would be shadowed
        # by the shell. Newer FastAPI wraps included routers lazily, older
        # versions flatten them into plain routes; accept either shape.
        from app.admin_spa import router as spa_router
        last = app.routes[-1]
        assert getattr(last, "original_router", None) is spa_router or getattr(last, "path", "") == "/admin/{path:path}"


class TestReports:
    async def test_lists_open_reports_with_counts(self, client):
        await sign_in(client)
        await seed_report(client)
        await seed_report(client, status="reviewed")
        r = (await client.get("/admin/api/reports")).json()
        assert r["counts"] == {"open": 1, "reviewed": 1, "dismissed": 0}
        assert [i["status"] for i in r["items"]] == ["open"]
        assert r["items"][0]["reasonLabel"] == "Misleading / Clickbait"

    async def test_write_without_csrf_header_is_refused(self, client):
        await sign_in(client)
        rid = await seed_report(client)
        r = await client.post(f"/admin/api/reports/{rid}", json={"action": "reviewed"})
        assert r.status_code == 403

    async def test_decide_and_bulk(self, client):
        csrf = await sign_in(client)
        a, b = await seed_report(client), await seed_report(client)
        h = {"X-CSRF-Token": csrf}
        assert (await client.post(f"/admin/api/reports/{a}", json={"action": "dismissed"}, headers=h)).status_code == 200
        r = await client.post("/admin/api/reports/bulk", json={"ids": [a, b], "action": "reviewed"}, headers=h)
        assert r.json()["updated"] == 2
        counts = (await client.get("/admin/api/reports?status=all")).json()["counts"]
        assert counts == {"open": 0, "reviewed": 2, "dismissed": 0}


class TestOverview:
    async def test_badges_count_what_is_waiting(self, client):
        await sign_in(client)
        await seed_report(client)
        async with client.session_factory() as session:
            session.add(Feedback(category="bug", message="Crash", source="web"))
            await session.commit()
        o = (await client.get("/admin/api/overview")).json()
        assert o["badges"]["reports"] == 1
        assert o["badges"]["feedback"] == 1
        assert o["badges"]["polls"] == 0
        assert o["tasks"]["poll"]["exists"] is False


async def seed_user(client, uid, name, email, days_ago=0):
    async with client.session_factory() as session:
        session.add(User(id=uid, email=email, display_name=name, provider="google",
                         created_at=utc_now() - timedelta(days=days_ago)))
        await session.commit()


class TestUsers:
    async def test_pages_searches_and_sorts_on_the_server(self, client):
        await sign_in(client)
        await seed_user(client, "u1", "Asha Rao", "asha@example.com", days_ago=3)
        await seed_user(client, "u2", "Bilal Khan", "bilal@example.com", days_ago=1)
        await seed_user(client, "u3", "Chitra Das", "chitra@example.com", days_ago=20)
        r = (await client.get("/admin/api/users?start=0&length=2")).json()
        assert r["total"] == 3 and r["filtered"] == 3
        assert [u["id"] for u in r["rows"]] == ["u2", "u1"]  # newest first by default
        r = (await client.get("/admin/api/users?q=chitra")).json()
        assert r["filtered"] == 1 and r["rows"][0]["email"] == "chitra@example.com"
        r = (await client.get("/admin/api/users?orderBy=displayName&dir=asc")).json()
        assert [u["displayName"] for u in r["rows"]] == ["Asha Rao", "Bilal Khan", "Chitra Das"]

    async def test_rows_carry_donations_saves_and_trial(self, client):
        await sign_in(client)
        await seed_user(client, "u1", "Asha Rao", "asha@example.com")
        async with client.session_factory() as session:
            session.add(Donation(user_id="u1", amount_paise=14900, provider="play", provider_payment_id="p1"))
            session.add(Donation(user_id="u1", amount_paise=4900, provider="play", provider_payment_id="p2", status="failed"))
            session.add(SavedStory(user_id="u1", cluster_id=1))
            session.add(PremiumTrial(user_id="u1", started_at=utc_now(), ends_at=utc_now() + timedelta(days=7)))
            await session.commit()
        row = (await client.get("/admin/api/users")).json()["rows"][0]
        assert row["donatedInr"] == 149.0
        assert row["saved"] == 1
        assert row["trial"] == "active"

    async def test_stats(self, client):
        await sign_in(client)
        await seed_user(client, "u1", "Asha", "a@example.com", days_ago=2)
        await seed_user(client, "u2", "Bilal", "b@example.com", days_ago=40)
        s = (await client.get("/admin/api/users/stats")).json()
        assert s["total"] == 2 and s["joinedThisWeek"] == 1
        assert len(s["signups"]) == 30 and sum(d["count"] for d in s["signups"]) == 1


class TestDonations:
    async def test_totals_months_and_list(self, client):
        await sign_in(client)
        async with client.session_factory() as session:
            session.add(Donation(amount_paise=14900, provider="play", provider_payment_id="p1"))
            session.add(Donation(amount_paise=49900, provider="play", provider_payment_id="p2",
                                 created_at=utc_now() - timedelta(days=45)))
            session.add(Donation(amount_paise=4900, provider="play", provider_payment_id="p3", status="refunded"))
            await session.commit()
        d = (await client.get("/admin/api/donations")).json()
        assert d["allTime"]["count"] == 2 and d["allTime"]["inr"] == 648.0
        assert d["last30Days"] == {"count": 1, "inr": 149.0}
        assert len(d["months"]) == 12 and sum(m["inr"] for m in d["months"]) == 648.0
        assert d["months"][-1]["month"] == utc_now().astimezone(__import__("app.services.polls", fromlist=["IST"]).IST).strftime("%Y-%m")
        assert len(d["items"]) == 3


class TestActivity:
    async def test_counts_distinct_readers_per_day(self, client):
        await sign_in(client)
        async with client.session_factory() as session:
            for i, uid in enumerate(["u1", "u1", "u2"]):
                session.add(ReadEvent(user_id=uid, cluster_id=1, event_id=f"e{i}"))
            await session.commit()
        a = (await client.get("/admin/api/overview/activity")).json()
        assert len(a["days"]) == 14
        assert a["days"][-1]["readers"] == 2 and a["days"][-1]["opens"] == 3
