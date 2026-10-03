"""Premium trial reminder: one push on the India-calendar day before a trial
ends (09:00–21:00 IST — see premium_trial.reminder_due), deep-linking to the
paywall. Driven from scripts/send_notifications.py, isolated like the other
steps there.

No notification switch: it's a one-off message about the user's own
account. Skipped for anyone whose purchase was verified (converted_at).
Dedup is a claim on PremiumTrial.reminder_sent_at, committed before the
push, so two overlapping runs can't both send it.
"""
import logging
from datetime import datetime
from typing import Awaitable, Callable

from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from app.models import PremiumTrial, User
from app.services.premium_trial import reminder_due

logger = logging.getLogger(__name__)

TRIAL_CHANNEL = "premium_trial"

TITLE = "Your Premium trial ends tomorrow"
BODY = "Keep timelines, explainers and the game archive. ₹490 once, no subscription."

# (device_token, title, body, cluster_id, channel_id, extra) -> delivered?
SendFn = Callable[[str, str, str, int, str, dict], Awaitable[bool]]


async def send_trial_reminders(session, now: datetime, send: SendFn) -> int:
    trials = (
        await session.execute(
            select(PremiumTrial).where(
                PremiumTrial.converted_at.is_(None),
                PremiumTrial.reminder_sent_at.is_(None),
                PremiumTrial.ends_at > now,
            )
        )
    ).scalars().all()

    sent = 0
    for trial in trials:
        if not reminder_due(trial, now):
            continue

        claimed = await session.execute(
            update(PremiumTrial)
            .where(PremiumTrial.user_id == trial.user_id, PremiumTrial.reminder_sent_at.is_(None))
            .values(reminder_sent_at=now)
        )
        await session.commit()
        if claimed.rowcount == 0:
            continue

        user = (
            await session.execute(
                select(User).options(selectinload(User.device_tokens)).where(User.id == trial.user_id)
            )
        ).scalar_one_or_none()
        if user is None or not user.device_tokens:
            continue

        for device in list(user.device_tokens):
            ok = await send(device.fcm_token, TITLE, BODY, 0, TRIAL_CHANNEL, {"type": "trial_reminder"})
            if not ok:
                await session.delete(device)
        await session.commit()
        sent += 1
    return sent
