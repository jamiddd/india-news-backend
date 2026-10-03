"""
The poll question bank JSON API — /admin/api/poll-bank, backed by PollFallback
(the admin SPA's Poll bank page).

Covers the flows an admin actually uses: the built-in polls appear (and adding
one can't suppress that seed), Claude drafts come back for review rather than
landing in the table, bad input is rejected before it can reach
activate_poll(), and every state change requires the CSRF header.
"""
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.database import get_db
from app.main import app
from app.models import PollFallback
from app.services import polls
from app.services.polls import FALLBACKS

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
        await conn.run_sync(PollFallback.__table__.create)
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
    """Signs in through the SPA login and returns the CSRF header for writes."""
    r = await client.post("/admin/api/login", json={"username": USERNAME, "password": PASSWORD})
    assert r.status_code == 200
    csrf = (await client.get("/admin/api/session")).json()["csrf"]
    return {"X-CSRF-Token": csrf}


async def bank_rows(client):
    async with client.session_factory() as session:
        return (await session.execute(select(PollFallback).order_by(PollFallback.id))).scalars().all()


async def seed(client):
    async with client.session_factory() as session:
        await polls.seed_fallbacks(session)


VALID = {
    "question": "Should every district have a public science museum?",
    "context": "Science museums support informal learning outside the classroom.",
    "options": ["Yes", "Only in larger districts", "No", ""],
    "category": "education",
}
BANK = "/admin/api/poll-bank"


class TestAccess:
    async def test_reads_need_a_session(self, client):
        assert (await client.get(BANK)).status_code == 401

    async def test_rejects_a_write_without_the_csrf_header(self, client):
        await sign_in(client)
        r = await client.post(BANK, json=VALID)
        assert r.status_code == 403
        assert await bank_rows(client) == []


class TestList:
    async def test_seeds_the_built_in_polls_so_they_are_visible(self, client):
        await sign_in(client)
        r = (await client.get(BANK)).json()
        assert r["total"] == len(FALLBACKS) and r["active"] == len(FALLBACKS)
        assert FALLBACKS[0][0] in [item["question"] for item in r["items"]]
        item = r["items"][0]
        assert {"id", "question", "context", "options", "category", "active", "usedCount", "createdAt"} <= item.keys()

    async def test_reports_when_no_poll_is_active(self, client):
        await sign_in(client)
        await seed(client)
        async with client.session_factory() as session:
            for row in (await session.execute(select(PollFallback))).scalars():
                row.active = False
            await session.commit()
        assert (await client.get(BANK)).json()["active"] == 0


class TestAdd:
    async def test_saves_a_valid_poll_and_drops_blank_options(self, client):
        h = await sign_in(client)
        r = await client.post(BANK, json=VALID, headers=h)
        assert r.status_code == 200
        assert r.json()["item"]["question"] == VALID["question"]
        added = (await bank_rows(client))[-1]
        assert added.question == VALID["question"]
        assert added.options == ["Yes", "Only in larger districts", "No"]
        assert added.category == "education"
        assert added.active is True and added.used_count == 0 and added.created_at is not None

    async def test_adding_to_an_empty_table_still_seeds_the_built_ins(self, client):
        h = await sign_in(client)
        await client.post(BANK, json=VALID, headers=h)
        assert len(await bank_rows(client)) == len(FALLBACKS) + 1

    async def test_rejects_a_single_option_poll(self, client):
        h = await sign_in(client)
        await seed(client)
        before = len(await bank_rows(client))
        r = await client.post(BANK, json={**VALID, "options": ["Yes", "", ""]}, headers=h)
        assert r.status_code == 422
        assert "2-4 distinct options" in r.json()["detail"]
        assert len(await bank_rows(client)) == before

    async def test_rejects_duplicate_options(self, client):
        h = await sign_in(client)
        r = await client.post(BANK, json={**VALID, "options": ["Yes", "yes"]}, headers=h)
        assert r.status_code == 422
        assert "distinct" in r.json()["detail"]

    async def test_rejects_a_question_already_in_the_bank(self, client):
        h = await sign_in(client)
        await seed(client)
        before = len(await bank_rows(client))
        r = await client.post(BANK, json={**VALID, "question": FALLBACKS[0][0].upper()}, headers=h)
        assert r.status_code == 409
        assert "already in the bank" in r.json()["detail"]
        assert len(await bank_rows(client)) == before

    async def test_rejects_an_over_long_category(self, client):
        h = await sign_in(client)
        r = await client.post(BANK, json={**VALID, "category": "x" * 51}, headers=h)
        assert r.status_code == 422
        assert "Category must be 50 characters or fewer" in r.json()["detail"]

    async def test_rejects_more_than_four_options(self, client):
        h = await sign_in(client)
        r = await client.post(BANK, json={**VALID, "options": ["A", "B", "C", "D", "E"]}, headers=h)
        assert r.status_code == 422
        assert await bank_rows(client) == []


class TestGenerate:
    async def test_returns_the_draft_without_saving_it(self, client, monkeypatch):
        async def fake(category, existing):
            assert category == "environment"
            assert FALLBACKS[0][0] in existing
            return {"question": "Should cities plant more street trees?", "context": "Trees cool streets.",
                    "options": ["Yes", "No"], "category": category}
        monkeypatch.setattr("app.poll_bank_admin.draft_bank_poll", fake)
        h = await sign_in(client)
        await seed(client)
        before = len(await bank_rows(client))
        r = await client.post(f"{BANK}/generate", json={"category": " environment "}, headers=h)
        assert r.status_code == 200
        assert r.json()["draft"]["question"] == "Should cities plant more street trees?"
        assert len(await bank_rows(client)) == before

    async def test_reports_a_failed_claude_request(self, client, monkeypatch):
        async def fake(category, existing):
            assert category is None
            return None
        monkeypatch.setattr("app.poll_bank_admin.draft_bank_poll", fake)
        h = await sign_in(client)
        r = await client.post(f"{BANK}/generate", json={}, headers=h)
        assert r.status_code == 502
        assert "Claude request failed" in r.json()["detail"]

    async def test_reports_a_draft_that_fails_validation(self, client, monkeypatch):
        async def fake(category, existing):
            raise ValueError("Poll needs 2-4 distinct options")
        monkeypatch.setattr("app.poll_bank_admin.draft_bank_poll", fake)
        h = await sign_in(client)
        r = await client.post(f"{BANK}/generate", json={}, headers=h)
        assert r.status_code == 502
        assert "failed validation" in r.json()["detail"]


class TestToggleAndDelete:
    async def test_toggle_flips_active(self, client):
        h = await sign_in(client)
        await seed(client)
        first = (await bank_rows(client))[0]
        r = await client.post(f"{BANK}/{first.id}/toggle", headers=h)
        assert r.json()["active"] is False
        assert (await bank_rows(client))[0].active is False
        await client.post(f"{BANK}/{first.id}/toggle", headers=h)
        assert (await bank_rows(client))[0].active is True

    async def test_delete_removes_the_row(self, client):
        h = await sign_in(client)
        await seed(client)
        first = (await bank_rows(client))[0]
        assert (await client.delete(f"{BANK}/{first.id}", headers=h)).status_code == 200
        assert all(row.id != first.id for row in await bank_rows(client))

    async def test_delete_needs_the_csrf_header(self, client):
        await sign_in(client)
        await seed(client)
        first = (await bank_rows(client))[0]
        assert (await client.delete(f"{BANK}/{first.id}")).status_code == 403
        assert len(await bank_rows(client)) == len(FALLBACKS)

    async def test_unknown_id_is_a_404(self, client):
        h = await sign_in(client)
        assert (await client.post(f"{BANK}/9999/toggle", headers=h)).status_code == 404
        assert (await client.delete(f"{BANK}/9999", headers=h)).status_code == 404


class TestDraftBankPoll:
    async def test_returns_none_when_the_claude_request_fails(self, monkeypatch):
        async def fake(*args, **kwargs):
            return None
        monkeypatch.setattr(polls, "call_claude_json", fake)
        assert await polls.draft_bank_poll(None, []) is None

    async def test_validates_the_draft_and_repairs_the_question_mark(self, monkeypatch):
        async def fake(*args, **kwargs):
            return {"question": "Should libraries open later", "context": "Hours affect access.",
                    "options": ["Yes", "No"]}
        monkeypatch.setattr(polls, "call_claude_json", fake)
        draft = await polls.draft_bank_poll("libraries", [])
        assert draft == {"question": "Should libraries open later?", "context": "Hours affect access.",
                         "options": ["Yes", "No"], "category": "libraries"}

    async def test_rejects_an_unusable_draft(self, monkeypatch):
        async def fake(*args, **kwargs):
            return {"question": "Should libraries open later?", "context": "Hours affect access.",
                    "options": ["Only one"]}
        monkeypatch.setattr(polls, "call_claude_json", fake)
        with pytest.raises(ValueError, match="2-4 distinct options"):
            await polls.draft_bank_poll(None, [])

    async def test_passes_category_and_avoid_list_to_the_prompt(self, monkeypatch):
        seen = {}

        async def fake(system, user_content, **kwargs):
            seen["system"], seen["user"] = system, user_content
            return None
        monkeypatch.setattr(polls, "call_claude_json", fake)
        await polls.draft_bank_poll("transport", ["Should buses be free?"])
        assert seen["system"] == polls.POLL_BANK_SYSTEM
        assert "transport" in seen["user"] and "- Should buses be free?" in seen["user"]
