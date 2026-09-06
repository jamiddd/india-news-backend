"""
The website feedback form — POST /api/v1/feedback and the page that posts to it.

Runs against a real (in-memory SQLite) database rather than a mocked session,
because every interesting assertion here is about what ends up in the table:
that the honeypot submission writes no row at all, that blank form fields land
as NULL instead of "", and that a discarded submission is indistinguishable
from an accepted one to the caller. A mocked session would pass all of these
while the endpoint did something else entirely.
"""
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import get_db
from app.main import app
from app.models import Feedback

GOOD = "The sports tab has not loaded on my Pixel since this morning."


@pytest.fixture(autouse=True)
def no_rate_limit(monkeypatch):
    from app.main import limiter
    monkeypatch.setattr(limiter, "enabled", False)

# The endpoint is rate limited to 5/hour per IP, and slowapi's counter is
# process-wide: every test here shares one budget and one client address, so
# past the fifth POST in the session every later test would get a 429 and fail
# for a reason that has nothing to do with what it asserts — and which test drew
# the short straw would shift every time one is added. The limiter is therefore
# switched off for this module (restored afterwards), and the limit itself is
# covered separately in TestRateLimit below.


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
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c.session_factory = Session
        yield c
    app.dependency_overrides.clear()


async def rows(client):
    async with client.session_factory() as session:
        return (await session.execute(select(Feedback).order_by(Feedback.id))).scalars().all()


class TestSubmission:
    async def test_stores_a_valid_message(self, client):
        r = await client.post("/api/v1/feedback", json={"category": "bug", "message": GOOD})
        assert r.status_code == 201
        (row,) = await rows(client)
        assert row.message == GOOD
        assert row.category == "bug"
        # Server-owned, never taken from the request body.
        assert row.source == "web"
        assert row.status == "new"

    async def test_name_and_email_are_optional(self, client):
        r = await client.post("/api/v1/feedback", json={"message": GOOD})
        assert r.status_code == 201
        (row,) = await rows(client)
        assert row.name is None and row.email is None

    async def test_blank_strings_become_null(self, client):
        """The form posts "" for a field left alone; "" is not an email address."""
        await client.post("/api/v1/feedback", json={"message": GOOD, "name": "  ", "email": ""})
        (row,) = await rows(client)
        assert row.name is None and row.email is None

    async def test_surrounding_whitespace_is_trimmed(self, client):
        await client.post("/api/v1/feedback",
                          json={"message": f"  {GOOD}  ", "name": " Asha ", "email": " a@b.com "})
        (row,) = await rows(client)
        assert (row.message, row.name, row.email) == (GOOD, "Asha", "a@b.com")

    async def test_defaults_to_the_other_category(self, client):
        await client.post("/api/v1/feedback", json={"message": GOOD})
        (row,) = await rows(client)
        assert row.category == "other"


class TestSource:
    async def test_defaults_to_web(self, client):
        await client.post("/api/v1/feedback", json={"message": GOOD})
        (row,) = await rows(client)
        assert row.source == "web"

    async def test_marks_a_submission_from_the_app(self, client):
        """Only the Android client sends X-Client-Version, so it is the one
        origin signal a caller cannot simply assert in the body."""
        await client.post("/api/v1/feedback", json={"message": GOOD},
                          headers={"X-Client-Version": "3"})
        (row,) = await rows(client)
        assert row.source == "android"

    async def test_the_body_cannot_claim_a_source(self, client):
        await client.post("/api/v1/feedback", json={"message": GOOD, "source": "android"})
        (row,) = await rows(client)
        assert row.source == "web"

    async def test_keeps_a_signed_in_user_id(self, client):
        await client.post("/api/v1/feedback",
                          json={"message": GOOD, "user_id": "usr_abc123"},
                          headers={"X-Client-Version": "3"})
        (row,) = await rows(client)
        assert row.user_id == "usr_abc123"


class TestRejection:
    @pytest.mark.parametrize("message", ["", "hi", "x" * 5001])
    async def test_rejects_messages_that_are_too_short_or_too_long(self, client, message):
        r = await client.post("/api/v1/feedback", json={"message": message})
        assert r.status_code == 422
        assert await rows(client) == []

    async def test_rejects_an_unknown_category(self, client):
        r = await client.post("/api/v1/feedback", json={"category": "spam", "message": GOOD})
        assert r.status_code == 422
        assert await rows(client) == []


class TestHoneypot:
    async def test_a_filled_honeypot_writes_no_row(self, client):
        await client.post("/api/v1/feedback",
                          json={"message": GOOD, "website": "http://spam.example"})
        assert await rows(client) == []

    async def test_and_is_indistinguishable_from_an_accepted_one(self, client):
        """
        Telling a spam script it was caught is free information for tuning the
        next attempt, so the response must match the real one exactly.
        """
        spam = await client.post("/api/v1/feedback",
                                 json={"message": GOOD, "website": "x"})
        real = await client.post("/api/v1/feedback", json={"message": GOOD})
        assert (spam.status_code, spam.json()) == (real.status_code, real.json())


class TestPage:
    async def test_serves_the_form(self, client):
        r = await client.get("/feedback")
        assert r.status_code == 200
        assert 'id="feedback-form"' in r.text
        # The honeypot has to be in the markup or the endpoint's defence is dead
        # weight; it is the one field the page cannot lose silently.
        assert 'id="fb-website"' in r.text


class TestRateLimit:
    """
    The only thing standing between an open, unauthenticated POST and an
    unbounded table, so it is worth one test that actually exercises it.
    """

    @pytest.fixture(autouse=True)
    def rate_limit_on(self, monkeypatch):
        from app.main import limiter
        monkeypatch.setattr(limiter, "enabled", True)
        limiter.reset()

    async def test_stops_accepting_after_five_in_an_hour(self, client):
        codes = [
            (await client.post("/api/v1/feedback", json={"message": GOOD})).status_code
            for _ in range(7)
        ]
        assert codes == [201, 201, 201, 201, 201, 429, 429]
        assert len(await rows(client)) == 5
