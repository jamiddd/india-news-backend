"""
The feedback triage page — /admin/feedback.

Covers the two things that would actually hurt: that the page is not readable
without signing in (it displays email addresses people gave us in confidence),
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


async def sign_in(client):
    r = await client.post("/admin/feedback/login",
                          data={"username": USERNAME, "password": PASSWORD},
                          follow_redirects=False)
    assert r.status_code == 303
    return r


def csrf_from(html_text):
    marker = "name=csrf value='"
    start = html_text.index(marker) + len(marker)
    return html_text[start:html_text.index("'", start)]


class TestAccess:
    async def test_redirects_to_login_when_signed_out(self, client):
        r = await client.get("/admin/feedback", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/admin/feedback/login"

    async def test_does_not_leak_a_message_to_a_signed_out_visitor(self, client):
        await seed(client, email="someone@example.com")
        r = await client.get("/admin/feedback", follow_redirects=False)
        assert "someone@example.com" not in r.text

    async def test_rejects_the_wrong_password(self, client):
        r = await client.post("/admin/feedback/login",
                              data={"username": USERNAME, "password": "wrong"})
        assert "Sign in failed" in r.text

    async def test_shows_feedback_once_signed_in(self, client):
        await seed(client)
        await sign_in(client)
        r = await client.get("/admin/feedback")
        assert r.status_code == 200
        assert "The sports tab will not load." in r.text


class TestListing:
    async def test_defaults_to_the_new_tab(self, client):
        await seed(client, message="I am unread and should show.")
        await seed(client, message="I am closed and should not.", status="closed")
        await sign_in(client)
        r = await client.get("/admin/feedback")
        assert "I am unread and should show." in r.text
        assert "I am closed and should not." not in r.text

    async def test_filters_by_status(self, client):
        await seed(client, message="I am closed and should show.", status="closed")
        await sign_in(client)
        r = await client.get("/admin/feedback?status=closed")
        assert "I am closed and should show." in r.text

    async def test_falls_back_to_new_for_an_unknown_status(self, client):
        await seed(client, message="Still visible.")
        await sign_in(client)
        r = await client.get("/admin/feedback?status=nonsense")
        assert "Still visible." in r.text

    async def test_marks_anonymous_feedback_as_such(self, client):
        await seed(client)
        await sign_in(client)
        r = await client.get("/admin/feedback")
        assert "anonymous" in r.text

    async def test_flags_a_sender_who_wants_a_reply(self, client):
        await seed(client, name="Asha", email="asha@example.com")
        await sign_in(client)
        r = await client.get("/admin/feedback")
        assert "wants a reply" in r.text
        assert "mailto:asha@example.com" in r.text

    async def test_escapes_html_in_a_message(self, client):
        """The message is attacker-controlled text on a page holding a session."""
        await seed(client, message="<script>alert('xss')</script> and more text")
        await sign_in(client)
        r = await client.get("/admin/feedback")
        assert "<script>alert" not in r.text
        assert "&lt;script&gt;" in r.text


class TestTriage:
    async def test_marks_an_item_read(self, client):
        item_id = await seed(client)
        await sign_in(client)
        page = await client.get("/admin/feedback")
        r = await client.post("/admin/feedback/update",
                              data={"csrf": csrf_from(page.text), "feedback_id": item_id,
                                    "action": "read", "back": "new"},
                              follow_redirects=False)
        assert r.status_code == 303
        async with client.session_factory() as session:
            row = (await session.execute(select(Feedback))).scalar_one()
        assert row.status == "read"

    async def test_returns_to_the_tab_you_were_on(self, client):
        item_id = await seed(client)
        await sign_in(client)
        page = await client.get("/admin/feedback")
        r = await client.post("/admin/feedback/update",
                              data={"csrf": csrf_from(page.text), "feedback_id": item_id,
                                    "action": "closed", "back": "new"},
                              follow_redirects=False)
        assert r.headers["location"] == "/admin/feedback?status=new"

    async def test_rejects_a_missing_csrf_token(self, client):
        item_id = await seed(client)
        await sign_in(client)
        r = await client.post("/admin/feedback/update",
                              data={"feedback_id": item_id, "action": "closed"})
        assert r.status_code == 403
        async with client.session_factory() as session:
            row = (await session.execute(select(Feedback))).scalar_one()
        assert row.status == "new"

    async def test_rejects_an_invalid_action(self, client):
        item_id = await seed(client)
        await sign_in(client)
        page = await client.get("/admin/feedback")
        r = await client.post("/admin/feedback/update",
                              data={"csrf": csrf_from(page.text), "feedback_id": item_id,
                                    "action": "deleted"})
        assert r.status_code == 400
