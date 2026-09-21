"""
The poll question bank — /admin/poll-bank, backed by PollFallback.

Covers the flows an admin actually uses: the built-in polls appear (and adding
one can't suppress that seed), Claude drafts land in the form for review rather
than the table, bad input is rejected before it can reach activate_poll(), and
every state change requires the CSRF token.
"""
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
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


async def sign_in(client):
    r = await client.post("/admin/polls/login",
                          data={"username": USERNAME, "password": PASSWORD},
                          follow_redirects=False)
    assert r.status_code == 303


def csrf_from(html_text):
    marker = "name=csrf value='"
    start = html_text.index(marker) + len(marker)
    return html_text[start:html_text.index("'", start)]


async def csrf_token(client):
    return csrf_from((await client.get("/admin/poll-bank")).text)


async def bank_rows(client):
    async with client.session_factory() as session:
        return (await session.execute(select(PollFallback).order_by(PollFallback.id))).scalars().all()


VALID = {
    "question": "Should every district have a public science museum?",
    "context": "Science museums support informal learning outside the classroom.",
    "option_0": "Yes", "option_1": "Only in larger districts", "option_2": "No", "option_3": "",
    "category": "education",
}


class TestAccess:
    async def test_redirects_to_login_when_signed_out(self, client):
        r = await client.get("/admin/poll-bank", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/admin/quiz/login"

    async def test_rejects_a_post_without_the_csrf_token(self, client):
        await sign_in(client)
        r = await client.post("/admin/poll-bank/add", data=VALID)
        assert r.status_code == 403
        assert await bank_rows(client) == []


class TestDashboard:
    async def test_seeds_the_built_in_polls_so_they_are_visible(self, client):
        await sign_in(client)
        r = await client.get("/admin/poll-bank?tab=list")
        assert f"{len(FALLBACKS)} poll(s) · {len(FALLBACKS)} active" in r.text
        assert FALLBACKS[0][0] in r.text

    async def test_warns_when_no_poll_is_active(self, client):
        await sign_in(client)
        await client.get("/admin/poll-bank")
        async with client.session_factory() as session:
            for row in (await session.execute(select(PollFallback))).scalars():
                row.active = False
            await session.commit()
        r = await client.get("/admin/poll-bank")
        assert "No active polls" in r.text


class TestAdd:
    async def test_saves_a_valid_poll_and_drops_blank_options(self, client):
        await sign_in(client)
        csrf = await csrf_token(client)
        r = await client.post("/admin/poll-bank/add", data={**VALID, "csrf": csrf}, follow_redirects=False)
        assert r.status_code == 303
        added = (await bank_rows(client))[-1]
        assert added.question == VALID["question"]
        assert added.options == ["Yes", "Only in larger districts", "No"]
        assert added.category == "education"
        assert added.active is True and added.used_count == 0 and added.created_at is not None

    async def test_adding_to_an_empty_table_still_seeds_the_built_ins(self, client):
        await sign_in(client)
        r = await client.get("/admin/poll-bank")  # sign-in cookie set; table not yet seeded by anything else
        csrf = csrf_from(r.text)
        async with client.session_factory() as session:
            await session.execute(PollFallback.__table__.delete())
            await session.commit()
        await client.post("/admin/poll-bank/add", data={**VALID, "csrf": csrf})
        assert len(await bank_rows(client)) == len(FALLBACKS) + 1

    async def test_rejects_a_single_option_poll(self, client):
        await sign_in(client)
        csrf = await csrf_token(client)
        before = len(await bank_rows(client))
        r = await client.post("/admin/poll-bank/add", data={
            **VALID, "csrf": csrf, "option_1": "", "option_2": ""})
        assert "2-4 distinct options" in r.text
        assert VALID["question"] in r.text  # form keeps what was typed
        assert len(await bank_rows(client)) == before

    async def test_rejects_duplicate_options(self, client):
        await sign_in(client)
        csrf = await csrf_token(client)
        r = await client.post("/admin/poll-bank/add", data={
            **VALID, "csrf": csrf, "option_0": "Yes", "option_1": "yes", "option_2": ""})
        assert "distinct" in r.text

    async def test_rejects_a_question_already_in_the_bank(self, client):
        await sign_in(client)
        csrf = await csrf_token(client)
        before = len(await bank_rows(client))
        r = await client.post("/admin/poll-bank/add", data={
            **VALID, "csrf": csrf, "question": FALLBACKS[0][0].upper()})
        assert "already in the bank" in r.text
        assert len(await bank_rows(client)) == before

    async def test_rejects_an_over_long_category(self, client):
        await sign_in(client)
        csrf = await csrf_token(client)
        r = await client.post("/admin/poll-bank/add", data={**VALID, "csrf": csrf, "category": "x" * 51})
        assert "Category must be 50 characters or fewer" in r.text

    async def test_escapes_html_in_what_it_echoes_back(self, client):
        await sign_in(client)
        csrf = await csrf_token(client)
        r = await client.post("/admin/poll-bank/add", data={
            **VALID, "csrf": csrf, "question": "<script>alert(1)</script>", "option_1": "", "option_2": ""})
        assert "<script>alert(1)</script>" not in r.text


class TestGenerate:
    async def test_puts_the_draft_in_the_form_without_saving_it(self, client, monkeypatch):
        async def fake(category, existing):
            assert category == "environment"
            assert FALLBACKS[0][0] in existing
            return {"question": "Should cities plant more street trees?", "context": "Trees cool streets.",
                    "options": ["Yes", "No"], "category": category}
        monkeypatch.setattr("app.poll_bank_admin.draft_bank_poll", fake)
        await sign_in(client)
        csrf = await csrf_token(client)
        async with client.session_factory() as session:
            await polls.seed_fallbacks(session)
        before = len(await bank_rows(client))
        r = await client.post("/admin/poll-bank/generate", data={"csrf": csrf, "gen_category": "environment"})
        assert "Should cities plant more street trees?" in r.text
        assert len(await bank_rows(client)) == before

    async def test_reports_a_failed_claude_request(self, client, monkeypatch):
        async def fake(category, existing):
            return None
        monkeypatch.setattr("app.poll_bank_admin.draft_bank_poll", fake)
        await sign_in(client)
        csrf = await csrf_token(client)
        r = await client.post("/admin/poll-bank/generate", data={"csrf": csrf})
        assert "Claude request failed" in r.text

    async def test_reports_a_draft_that_fails_validation(self, client, monkeypatch):
        async def fake(category, existing):
            raise ValueError("Poll needs 2-4 distinct options")
        monkeypatch.setattr("app.poll_bank_admin.draft_bank_poll", fake)
        await sign_in(client)
        csrf = await csrf_token(client)
        r = await client.post("/admin/poll-bank/generate", data={"csrf": csrf})
        assert "failed validation" in r.text


class TestToggleAndDelete:
    async def test_toggle_flips_active(self, client):
        await sign_in(client)
        csrf = await csrf_token(client)
        first = (await bank_rows(client))[0]
        await client.post(f"/admin/poll-bank/{first.id}/toggle", data={"csrf": csrf})
        assert (await bank_rows(client))[0].active is False
        await client.post(f"/admin/poll-bank/{first.id}/toggle", data={"csrf": csrf})
        assert (await bank_rows(client))[0].active is True

    async def test_delete_removes_the_row(self, client):
        await sign_in(client)
        csrf = await csrf_token(client)
        first = (await bank_rows(client))[0]
        await client.post(f"/admin/poll-bank/{first.id}/delete", data={"csrf": csrf})
        assert all(row.id != first.id for row in await bank_rows(client))

    async def test_unknown_id_is_a_404(self, client):
        await sign_in(client)
        csrf = await csrf_token(client)
        assert (await client.post("/admin/poll-bank/9999/toggle", data={"csrf": csrf})).status_code == 404


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
