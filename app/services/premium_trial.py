"""The 7-day free Premium trial.

Tap-to-start, no payment method, nothing auto-charges. One trial per
account, ever (PremiumTrial.user_id is the primary key). Premium itself is
still granted client-side — the app reads this trial's window and unlocks
Premium until ends_at — so this module only owns the window and the two
facts the server needs about it: whether the day-before reminder went out,
and whether a verified purchase followed (converted_at), which suppresses
that reminder.

The reminder push lives in app/services/trial_notifications.py.
"""
from datetime import datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.models import PremiumTrial

IST = ZoneInfo("Asia/Kolkata")

TRIAL_LENGTH = timedelta(days=7)

# The reminder goes out on the India-calendar day before the trial ends,
# inside daytime hours, so "ends tomorrow" is always true and nobody gets a
# billing-ish push at 2am.
REMIND_FROM = time(9, 0)
REMIND_UNTIL = time(21, 0)

ELIGIBLE = "eligible"
ACTIVE = "active"
ENDED = "ended"
CONVERTED = "converted"


def _aware(value: datetime) -> datetime:
    # SQLite (tests) hands timestamptz back naive; Postgres never does.
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def trial_status(trial: Optional[PremiumTrial], now: datetime) -> str:
    if trial is None:
        return ELIGIBLE
    if trial.converted_at is not None:
        return CONVERTED
    if now < _aware(trial.ends_at):
        return ACTIVE
    return ENDED


def reminder_due(trial: PremiumTrial, now: datetime) -> bool:
    if trial.converted_at is not None or trial.reminder_sent_at is not None:
        return False
    ends_at = _aware(trial.ends_at)
    if now >= ends_at:
        return False
    now_ist = now.astimezone(IST)
    end_day = ends_at.astimezone(IST).date()
    if now_ist.date() != end_day - timedelta(days=1):
        return False
    return REMIND_FROM <= now_ist.time() < REMIND_UNTIL


async def get_trial(session, user_id: str) -> Optional[PremiumTrial]:
    return (
        await session.execute(select(PremiumTrial).where(PremiumTrial.user_id == user_id))
    ).scalar_one_or_none()


async def start_trial(session, user_id: str, now: datetime) -> tuple[PremiumTrial, bool]:
    """Returns (trial, created). created is False when the account already
    had a trial — the existing row comes back untouched, never restarted."""
    existing = await get_trial(session, user_id)
    if existing is not None:
        return existing, False
    trial = PremiumTrial(user_id=user_id, started_at=now, ends_at=now + TRIAL_LENGTH)
    session.add(trial)
    try:
        await session.commit()
    except IntegrityError:
        # A concurrent start (double tap, two devices) won the primary key.
        await session.rollback()
        return await get_trial(session, user_id), False
    return trial, True


async def mark_converted(session, user_id: str, now: datetime) -> None:
    """Called after a verified purchase. A no-op for an account that never
    started a trial, or one already marked."""
    await session.execute(
        update(PremiumTrial)
        .where(PremiumTrial.user_id == user_id, PremiumTrial.converted_at.is_(None))
        .values(converted_at=now)
    )
    await session.commit()


def to_response(trial: Optional[PremiumTrial], now: datetime) -> dict:
    return {
        "status": trial_status(trial, now),
        "started_at": _aware(trial.started_at) if trial else None,
        "ends_at": _aware(trial.ends_at) if trial else None,
        "server_now": now,
    }
