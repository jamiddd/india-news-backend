from datetime import date

import pytest

from app.services.polls import poll_times, validate_draft


def test_poll_window_is_nine_to_nine_ist():
    publish, closes = poll_times(date(2026, 8, 10))
    assert publish.hour == 9
    assert closes - publish == __import__("datetime").timedelta(days=1)


def test_validates_balanced_poll_shape():
    question, context, options = validate_draft({
        "question": "Should cities reserve more road space for public transport?",
        "context": "The decision can affect congestion, access and travel times.",
        "options": ["Yes", "Only on major routes", "No"],
    })
    assert question.endswith("?")
    assert len(options) == 3


def test_rejects_duplicate_options():
    with pytest.raises(ValueError, match="distinct"):
        validate_draft({
            "question": "Should schools offer more practical financial education?",
            "context": "Students encounter financial decisions after leaving school.",
            "options": ["Yes", "yes"],
        })


def test_keeps_sensitive_topics_for_human_review():
    """No keyword blocklist here on purpose: a draft on a hard news topic must
    still be produced so the admin can accept or regenerate it, rather than
    silently collapsing the day's poll to a generic fallback."""
    question, _, _ = validate_draft({
        "question": "Should road-safety funding rise after the rise in highway accident deaths?",
        "context": "Highway safety spending is set annually alongside road construction budgets.",
        "options": ["Increase it", "Keep it unchanged", "Reduce it"],
    })
    assert "accident" in question


def test_accepts_long_sentence_options():
    """Long options are a layout concern, not a failure — the client wraps them,
    and rejecting them only costs us the day's real poll."""
    _, _, options = validate_draft({
        "question": "Should India's growth strategy keep prioritising headline GDP growth?",
        "context": "India retained its position as the fastest-growing large economy at 7.8%.",
        "options": [
            "Prioritise sustaining high GDP growth rates through current economic policies",
            "Shift focus toward reducing inequality and ensuring balanced regional development alongside growth",
        ],
    })
    assert max(len(option) for option in options) > 80


def test_appends_missing_question_mark():
    question, _, _ = validate_draft({
        "question": "Should cities reserve more road space for public transport",
        "context": "The decision can affect congestion, access and travel times.",
        "options": ["Yes", "No"],
    })
    assert question.endswith("?")



def test_poll_scheduler_runs_each_action_once_per_day():
    """The loop used to act twice per pass — a catch-up action, then a second
    one after the sleep, which the next pass then repeated. Doubled every
    draft and publish, and doubled the Claude retries on a failing day."""
    from datetime import datetime, time, timedelta
    from collections import Counter

    def simulate(start: datetime, passes: int = 40) -> list[tuple[str, date]]:
        """Mirrors main()'s scheduling decisions with a fake clock, where
        sleeping jumps straight to next_run."""
        now, log = start, []
        for _ in range(passes):
            draft_at = datetime.combine(now.date(), time(0, 10))
            publish_at = datetime.combine(now.date(), time(9))
            if now >= publish_at:
                log.append(("publish", now.date()))
                next_run = datetime.combine(now.date() + timedelta(days=1), time(0, 10))
            elif now >= draft_at:
                log.append(("prepare", now.date()))
                next_run = publish_at
            else:
                next_run = draft_at
            now = next_run
        return log

    for start_hour in (0, 5, 10, 23):
        log = simulate(datetime(2026, 9, 3, start_hour, 0))
        repeated = [entry for entry, count in Counter(log).items() if count > 1]
        assert not repeated, f"start {start_hour}:00 repeated {repeated}"


# ---------------------------------------------------------------------------
# Daily poll review JSON API — /admin/api/polls (app/poll_admin.py), the admin
# SPA's Poll of the Day page. Same sign-in + CSRF pattern as test_admin_api.py.
# ---------------------------------------------------------------------------
from datetime import datetime, timedelta, timezone  # noqa: E402

from fastapi import HTTPException  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import DailyPoll, PollOption  # noqa: E402
from app.services.polls import IST  # noqa: E402

POLLS = "/admin/api/polls"


@pytest.fixture
def admin_credentials(monkeypatch):
    monkeypatch.setattr(settings, "POLL_ADMIN_USERNAME", "reviewer")
    monkeypatch.setattr(settings, "POLL_ADMIN_PASSWORD", "correct-horse")
    monkeypatch.setattr(settings, "POLL_SESSION_SECRET", "test-secret")


@pytest.fixture
async def client(admin_credentials):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        for model in (DailyPoll, PollOption):
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


async def sign_in(client) -> dict:
    r = await client.post("/admin/api/login", json={"username": "reviewer", "password": "correct-horse"})
    assert r.status_code == 200
    return {"X-CSRF-Token": (await client.get("/admin/api/session")).json()["csrf"]}


async def seed_poll(client, status="draft", publish_in=timedelta(hours=2)) -> int:
    # UTC: SQLite drops tzinfo and the API reads naive values back as UTC.
    publish = datetime.now(timezone.utc) + publish_in
    async with client.session_factory() as session:
        poll = DailyPoll(poll_date=datetime.now(IST).date(), question="Should towns add more bus lanes?",
                         context="Bus lanes change road space.", status=status, source_cluster_id=7,
                         source_headline="City plans bus lanes", generation_method="ai",
                         publish_at=publish, closes_at=publish + timedelta(days=1))
        session.add(poll)
        await session.flush()
        session.add_all(PollOption(poll_id=poll.id, position=i, text=t) for i, t in enumerate(["Yes", "No"]))
        await session.commit()
        return poll.id


class TestDailyPollAdmin:
    async def test_reads_need_a_session(self, client):
        assert (await client.get(POLLS)).status_code == 401

    async def test_writes_need_the_csrf_header(self, client):
        await sign_in(client)
        poll_id = await seed_poll(client)
        assert (await client.post(f"{POLLS}/generate")).status_code == 403
        assert (await client.post(f"{POLLS}/{poll_id}/reject")).status_code == 403

    async def test_no_draft_yet(self, client):
        await sign_in(client)
        r = (await client.get(POLLS)).json()
        assert r["poll"] is None
        assert r["today"] == datetime.now(IST).date().isoformat()

    async def test_shows_an_editable_draft(self, client):
        await sign_in(client)
        await seed_poll(client)
        p = (await client.get(POLLS)).json()["poll"]
        assert p["options"] == ["Yes", "No"]
        assert p["editable"] is True
        assert p["sourceUrl"] == "/api/v1/clusters/7"
        assert p["sourceHeadline"] == "City plans bus lanes"

    async def test_past_publish_time_is_not_editable(self, client):
        await sign_in(client)
        await seed_poll(client, publish_in=timedelta(hours=-1))
        assert (await client.get(POLLS)).json()["poll"]["editable"] is False

    async def test_generate_creates_todays_draft(self, client, monkeypatch):
        calls = []

        async def fake(db, day, replace=False):
            calls.append((day, replace))
            publish = datetime.now(timezone.utc) + timedelta(hours=1)
            db.add(DailyPoll(poll_date=day, question="Q?", context="C.", status="draft",
                             publish_at=publish, closes_at=publish + timedelta(days=1)))
            await db.commit()
        monkeypatch.setattr("app.poll_admin.generate_draft", fake)
        h = await sign_in(client)
        r = await client.post(f"{POLLS}/generate", headers=h)
        assert r.status_code == 200
        assert r.json()["poll"]["question"] == "Q?"
        assert calls == [(datetime.now(IST).date(), False)]

    async def test_failed_generation_reports_why(self, client, monkeypatch):
        async def fake(db, day, replace=False):
            raise RuntimeError("No corroborated stories available")
        monkeypatch.setattr("app.poll_admin.generate_draft", fake)
        h = await sign_in(client)
        r = await client.post(f"{POLLS}/generate", headers=h)
        assert r.status_code == 502
        assert r.json()["detail"] == "Draft generation failed: No corroborated stories available"

    async def test_regenerate_replaces_and_passes_conflicts_through(self, client, monkeypatch):
        calls = []

        async def fake(db, day, replace=False):
            calls.append(replace)
            raise HTTPException(status_code=409, detail="Only a draft can be regenerated")
        monkeypatch.setattr("app.poll_admin.generate_draft", fake)
        h = await sign_in(client)
        r = await client.post(f"{POLLS}/regenerate", headers=h)
        assert r.status_code == 409
        assert r.json()["detail"] == "Only a draft can be regenerated"
        assert calls == [True]

    async def test_approve_sends_the_edits_without_blank_options(self, client, monkeypatch):
        seen = {}

        async def fake(db, poll_id, question, context, options):
            seen.update(poll_id=poll_id, question=question, context=context, options=options)
        monkeypatch.setattr("app.poll_admin.approve_poll", fake)
        h = await sign_in(client)
        poll_id = await seed_poll(client)
        r = await client.post(f"{POLLS}/{poll_id}/approve", headers=h,
                              json={"question": "Edited?", "context": "Edited.", "options": ["A", " ", "B", ""]})
        assert r.status_code == 200
        assert seen == {"poll_id": poll_id, "question": "Edited?", "context": "Edited.", "options": ["A", "B"]}

    async def test_approve_reports_validation_errors(self, client, monkeypatch):
        async def fake(db, poll_id, question, context, options):
            validate_draft({"question": question, "context": context, "options": options})
        monkeypatch.setattr("app.poll_admin.approve_poll", fake)
        h = await sign_in(client)
        poll_id = await seed_poll(client)
        r = await client.post(f"{POLLS}/{poll_id}/approve", headers=h,
                              json={"question": "Edited?", "context": "Edited.", "options": ["Only one"]})
        assert r.status_code == 422
        assert "2-4 distinct options" in r.json()["detail"]

    async def test_reject_switches_to_the_fallback_once(self, client):
        h = await sign_in(client)
        poll_id = await seed_poll(client)
        r = await client.post(f"{POLLS}/{poll_id}/reject", headers=h)
        assert r.status_code == 200
        assert r.json()["poll"]["status"] == "rejected"
        assert r.json()["poll"]["editable"] is False
        again = await client.post(f"{POLLS}/{poll_id}/reject", headers=h)
        assert again.status_code == 409
        async with client.session_factory() as session:
            assert (await session.scalar(select(DailyPoll.status))) == "rejected"
