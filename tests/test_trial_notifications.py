"""The day-before trial reminder: who gets it, and that it goes out once."""
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import DeviceToken, PremiumTrial, User
from app.services import premium_trial as pt
from app.services import trial_notifications as tn

UTC = timezone.utc
START = datetime(2026, 10, 1, 4, 11, tzinfo=UTC)          # Thu 1 Oct 09:41 IST
DUE = datetime(2026, 10, 7, 10, 0, tzinfo=pt.IST)          # Wed 7 Oct 10:00 IST


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        for table in (User.__table__, DeviceToken.__table__, PremiumTrial.__table__):
            await conn.run_sync(table.create)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        yield s


async def add_user(session, uid, **trial_fields):
    session.add(User(id=uid, email=f"{uid}@x.in", display_name=uid, provider="google", preferences={}))
    session.add(DeviceToken(user_id=uid, fcm_token=f"tok-{uid}"))
    session.add(PremiumTrial(user_id=uid, started_at=START, ends_at=START + pt.TRIAL_LENGTH, **trial_fields))
    await session.commit()


class Recorder:
    def __init__(self):
        self.calls = []

    async def __call__(self, token, title, body, cluster_id, channel_id, extra):
        self.calls.append((token, title, channel_id, extra))
        return True


async def test_sends_once_to_an_active_unconverted_trial(session):
    await add_user(session, "usr_a")
    send = Recorder()
    assert await tn.send_trial_reminders(session, DUE, send) == 1
    assert send.calls == [("tok-usr_a", tn.TITLE, "premium_trial", {"type": "trial_reminder"})]
    # A second run in the same window sends nothing.
    assert await tn.send_trial_reminders(session, DUE, send) == 0
    assert len(send.calls) == 1


async def test_skips_converted_and_not_yet_due(session):
    await add_user(session, "usr_bought", converted_at=datetime(2026, 10, 3, tzinfo=UTC))
    await add_user(session, "usr_a")
    send = Recorder()
    early = datetime(2026, 10, 6, 10, 0, tzinfo=pt.IST)
    assert await tn.send_trial_reminders(session, early, send) == 0
    assert await tn.send_trial_reminders(session, DUE, send) == 1
    assert [c[0] for c in send.calls] == ["tok-usr_a"]
