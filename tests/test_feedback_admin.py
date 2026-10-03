"""
The feedback triage API behind the admin SPA's inbox — /admin/api/feedback.

Covers the two things that would actually hurt: that nothing is readable
without signing in (it returns email addresses people gave us in confidence),
and that a status change cannot be driven by a forged cross-site POST.
"""
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.database import get_db
from app.main import app
from app.models import Feedback

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
        await conn.run_sync(Feedback.__table__.create)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def override():
        async with Session() as session:
            yield session

    app.dependency_overrides[get_db] = override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        c.session_factory = Session
        yield c
    app.dependency_overrides.clear()


async def seed(client, **kwargs):
    fields = {"category": "bug", "message": "The sports tab will not load.", "source": "web"}
    fields.update(kwargs)
    async with client.session_factory() as session:
        item = Feedback(**fields)
        session.add(item)
        await session.commit()
        return item.id


async def sign_in(client) -> str:
    r = await client.post("/admin/api/login", json={"username": USERNAME, "password": PASSWORD})
    assert r.status_code == 200
    return (await client.get("/admin/api/session")).json()["csrf"]


class TestAccess:
    async def test_refuses_a_signed_out_visitor(self, client):
        await seed(client, email="someone@example.com")
        r = await client.get("/admin/api/feedback")
        assert r.status_code == 401
        assert "someone@example.com" not in r.text

    async def test_rejects_the_wrong_password(self, client):
        r = await client.post("/admin/api/login", json={"username": USERNAME, "password": "wrong"})
        assert r.status_code == 401

    async def test_shows_feedback_once_signed_in(self, client):
        await seed(client)
        await sign_in(client)
        r = await client.get("/admin/api/feedback")
        assert r.status_code == 200
        assert r.json()["items"][0]["message"] == "The sports tab will not load."


class TestListing:
    async def test_defaults_to_the_new_tab(self, client):
        await seed(client, message="I am unread and should show.")
        await seed(client, message="I am closed and should not.", status="closed")
        await sign_in(client)
        r = (await client.get("/admin/api/feedback")).json()
        assert [i["message"] for i in r["items"]] == ["I am unread and should show."]
        assert r["counts"] == {"new": 1, "read": 0, "closed": 1}

    async def test_filters_by_status(self, client):
        await seed(client, message="I am closed and should show.", status="closed")
        await sign_in(client)
        r = (await client.get("/admin/api/feedback?status=closed")).json()
        assert [i["message"] for i in r["items"]] == ["I am closed and should show."]

    async def test_falls_back_to_new_for_an_unknown_status(self, client):
        await seed(client, message="Still visible.")
        await sign_in(client)
        r = (await client.get("/admin/api/feedback?status=nonsense")).json()
        assert [i["message"] for i in r["items"]] == ["Still visible."]

    async def test_searches_message_and_email(self, client):
        await seed(client, message="Audio stops at 2:10")
        await seed(client, message="Please add Assamese", email="asha@example.com")
        await sign_in(client)
        r = (await client.get("/admin/api/feedback?q=asha@")).json()
        assert [i["message"] for i in r["items"]] == ["Please add Assamese"]
        assert r["total"] == 1

    async def test_pages_newest_first(self, client):
        for n in range(3):
            await seed(client, message=f"message {n}")
        await sign_in(client)
        r = (await client.get("/admin/api/feedback?offset=1&limit=1")).json()
        assert r["total"] == 3
        assert [i["message"] for i in r["items"]] == ["message 1"]

    async def test_marks_anonymous_feedback_as_such(self, client):
        await seed(client)
        await sign_in(client)
        item = (await client.get("/admin/api/feedback")).json()["items"][0]
        assert item["name"] is None and item["email"] is None and item["userId"] is None

    async def test_flags_a_sender_who_wants_a_reply(self, client):
        await seed(client, name="Asha", email="asha@example.com")
        await sign_in(client)
        item = (await client.get("/admin/api/feedback")).json()["items"][0]
        assert item["wantsReply"] is True
        assert item["email"] == "asha@example.com"

    async def test_does_not_claim_an_app_sender_wants_a_reply(self, client):
        """The Android app fills email from the signed-in account, so an email
        on a source=android row is not a request for a reply."""
        await seed(client, source="android", email="asha@example.com", user_id="u-123")
        await sign_in(client)
        item = (await client.get("/admin/api/feedback")).json()["items"][0]
        assert item["wantsReply"] is False
        assert item["emailFromAccount"] is True
        assert item["userId"] == "u-123"

    async def test_serves_a_message_as_json_not_html(self, client):
        """The message is attacker-controlled text. The API returns it as JSON
        data and the SPA escapes it when rendering (sections/feedback.js)."""
        await seed(client, message="<script>alert('xss')</script> and more text")
        await sign_in(client)
        r = await client.get("/admin/api/feedback")
        assert r.headers["content-type"].startswith("application/json")
        assert r.json()["items"][0]["message"].startswith("<script>")


class TestTriage:
    async def test_marks_an_item_read(self, client):
        item_id = await seed(client)
        csrf = await sign_in(client)
        r = await client.post(f"/admin/api/feedback/{item_id}", json={"status": "read"},
                              headers={"X-CSRF-Token": csrf})
        assert r.status_code == 200
        async with client.session_factory() as session:
            row = (await session.execute(select(Feedback))).scalar_one()
        assert row.status == "read"

    async def test_bulk_closes(self, client):
        ids = [await seed(client), await seed(client)]
        csrf = await sign_in(client)
        r = await client.post("/admin/api/feedback/bulk", json={"ids": ids, "status": "closed"},
                              headers={"X-CSRF-Token": csrf})
        assert r.json()["updated"] == 2
        counts = (await client.get("/admin/api/feedback")).json()["counts"]
        assert counts == {"new": 0, "read": 0, "closed": 2}

    async def test_rejects_a_missing_csrf_token(self, client):
        item_id = await seed(client)
        await sign_in(client)
        r = await client.post(f"/admin/api/feedback/{item_id}", json={"status": "closed"})
        assert r.status_code == 403
        async with client.session_factory() as session:
            row = (await session.execute(select(Feedback))).scalar_one()
        assert row.status == "new"

    async def test_rejects_an_invalid_status(self, client):
        item_id = await seed(client)
        csrf = await sign_in(client)
        r = await client.post(f"/admin/api/feedback/{item_id}", json={"status": "deleted"},
                              headers={"X-CSRF-Token": csrf})
        assert r.status_code == 422
