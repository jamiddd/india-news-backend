"""
The 7-day Premium trial: the pure status/reminder rules, and the two
endpoints against a real (in-memory SQLite) database, since what matters is
what ends up in premium_trials — that a second start never restarts or
extends the first, and that another account's id is refused.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import get_db
from app.main import app
from app.models import PremiumTrial
from app.services import premium_trial as pt
from app.services.request_auth import CallerIdentity, require_user

UTC = timezone.utc


def trial(started, **kw):
    fields = dict(started_at=started, ends_at=started + pt.TRIAL_LENGTH, reminder_sent_at=None, converted_at=None)
    fields.update(kw)
    return SimpleNamespace(**fields)


# Thu 1 Oct 2026, 09:41 IST == 04:11 UTC; ends Thu 8 Oct 09:41 IST.
START = datetime(2026, 10, 1, 4, 11, tzinfo=UTC)


def ist(day, hh, mm=0):
    return datetime(2026, 10, day, hh, mm, tzinfo=pt.IST)


class TestStatus:
    def test_no_row_is_eligible(self):
        assert pt.trial_status(None, START) == pt.ELIGIBLE

    def test_active_until_the_exact_end(self):
        t = trial(START)
        assert pt.trial_status(t, t.ends_at - timedelta(seconds=1)) == pt.ACTIVE
        assert pt.trial_status(t, t.ends_at) == pt.ENDED

    def test_converted_wins_over_active_and_ended(self):
        t = trial(START, converted_at=START + timedelta(days=2))
        assert pt.trial_status(t, START + timedelta(days=3)) == pt.CONVERTED
        assert pt.trial_status(t, START + timedelta(days=30)) == pt.CONVERTED

    def test_naive_timestamps_are_read_as_utc(self):
        t = trial(START.replace(tzinfo=None))
        assert pt.trial_status(t, START + timedelta(days=1)) == pt.ACTIVE


class TestReminderDue:
    def test_due_the_day_before_inside_the_window(self):
        assert pt.reminder_due(trial(START), ist(7, 9, 0))
        assert pt.reminder_due(trial(START), ist(7, 20, 59))

    def test_not_outside_the_window(self):
        assert not pt.reminder_due(trial(START), ist(7, 8, 59))
        assert not pt.reminder_due(trial(START), ist(7, 21, 0))

    def test_not_on_other_days(self):
        assert not pt.reminder_due(trial(START), ist(6, 12))
        assert not pt.reminder_due(trial(START), ist(8, 9, 0))  # end day itself, before 09:41

    def test_early_morning_end_still_reminds_the_previous_day(self):
        # Ends 01:00 IST on the 8th -> remind on the 7th, ~10-16h before.
        start = ist(1, 1, 0).astimezone(UTC)
        assert pt.reminder_due(trial(start), ist(7, 15))
        assert not pt.reminder_due(trial(start), ist(8, 0, 30))

    def test_not_after_reminding_or_converting(self):
        assert not pt.reminder_due(trial(START, reminder_sent_at=ist(7, 9)), ist(7, 10))
        assert not pt.reminder_due(trial(START, converted_at=ist(3, 9)), ist(7, 10))


# --- endpoints -----------------------------------------------------------

@pytest.fixture(autouse=True)
def no_rate_limit(monkeypatch):
    from app.main import limiter
    monkeypatch.setattr(limiter, "enabled", False)


@pytest.fixture
async def client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        # No users table here, so SQLite doesn't enforce the FK (it's off by default).
        await conn.run_sync(PremiumTrial.__table__.create)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def override_db():
        async with Session() as session:
            yield session

    async def as_usr_a(user_id: str) -> CallerIdentity:
        if user_id != "usr_a":
            raise HTTPException(status_code=403, detail="Not your account")
        return CallerIdentity(user_id=user_id, firebase_uid="")

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[require_user] = as_usr_a
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        c.session_factory = Session
        yield c
    app.dependency_overrides.clear()


class TestEndpoints:
    async def test_new_account_is_eligible(self, client):
        r = await client.get("/api/v1/users/usr_a/trial")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "eligible"
        assert body["ends_at"] is None
        assert body["server_now"]

    async def test_start_then_active(self, client):
        r = await client.post("/api/v1/users/usr_a/trial/start")
        assert r.status_code == 201
        started = r.json()
        assert started["status"] == "active"
        ends = datetime.fromisoformat(started["ends_at"])
        begun = datetime.fromisoformat(started["started_at"])
        assert ends - begun == timedelta(days=7)

        r = await client.get("/api/v1/users/usr_a/trial")
        assert r.json()["status"] == "active"

    async def test_second_start_is_409_and_never_restarts(self, client):
        first = (await client.post("/api/v1/users/usr_a/trial/start")).json()
        r = await client.post("/api/v1/users/usr_a/trial/start")
        assert r.status_code == 409
        assert r.json()["detail"]["status"] == "active"
        async with client.session_factory() as s:
            (row,) = (await s.execute(select(PremiumTrial))).scalars().all()
        assert row.ends_at.replace(tzinfo=UTC) == datetime.fromisoformat(first["ends_at"])

    async def test_other_account_is_refused(self, client):
        assert (await client.get("/api/v1/users/usr_b/trial")).status_code == 403
        assert (await client.post("/api/v1/users/usr_b/trial/start")).status_code == 403

    async def test_mark_converted(self, client):
        await client.post("/api/v1/users/usr_a/trial/start")
        async with client.session_factory() as s:
            await pt.mark_converted(s, "usr_a", datetime.now(UTC))
        r = await client.get("/api/v1/users/usr_a/trial")
        assert r.json()["status"] == "converted"
