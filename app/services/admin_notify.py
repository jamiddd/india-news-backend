"""One push for the reviewer's whole day.

The poll and the quiz are two drafts, but one person, one sitting, one moment
in the morning. Two notifications would mean the second is either ignored as a
duplicate or actioned separately hours later — so this sends a single message
summarising both and deep-links to /admin, which lists them.

Timing works out without coordinating the two schedulers: the quiz draft for
day D is written by run_crossword_scheduler at 23:55 IST on D-1, and the poll
draft at 04:30 IST on D. By the time this fires (right after the poll draft),
both already exist.
"""
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import DeviceToken, User

logger = logging.getLogger(__name__)


def compose(tasks: dict[str, dict]) -> tuple[str, str] | None:
    """(title, body) for the day's review push, or None if nothing is waiting.

    Returning None matters: a notification that arrives every morning whether
    or not there is anything to do is one the reviewer learns to swipe away.
    """
    waiting = [name for name, task in tasks.items() if task["waiting"]]
    missing = [name for name, task in tasks.items() if not task["exists"]]

    if not waiting and not missing:
        return None

    if waiting and missing:
        title = f"{len(waiting)} draft to review, {len(missing)} missing"
    elif waiting:
        title = ("Poll and quiz drafts ready" if len(waiting) == 2
                 else f"{waiting[0].capitalize()} draft ready to review")
    else:
        title = f"{', '.join(name.capitalize() for name in missing)} draft missing"

    lines = []
    for name in ("poll", "quiz"):
        task = tasks[name]
        if task["waiting"]:
            lines.append(f"{name.capitalize()}: {task['summary']}")
        elif not task["exists"]:
            lines.append(f"{name.capitalize()}: not generated")
    return title, " · ".join(lines)


async def notify_admin_reviews_ready(session: AsyncSession, day: date) -> bool:
    """Send the day's single review push. Returns whether anything was sent.

    Best-effort throughout: a notification failure must never break draft
    generation, which is the job that actually matters.
    """
    # Imported here so the poll/quiz generation path does not depend on the
    # admin page module at import time.
    from app.admin_home import pending_reviews

    if not settings.ADMIN_USER_EMAIL:
        return False
    tasks = await pending_reviews(session, day)
    composed = compose(tasks)
    if composed is None:
        logger.info("Admin review push skipped for %s: nothing waiting", day)
        return False
    title, body = composed

    try:
        from firebase_admin import messaging
        from app.services.firebase_auth import _get_firebase_app
        app = _get_firebase_app()
    except Exception:
        return False

    admin = await session.scalar(select(User).where(User.email == settings.ADMIN_USER_EMAIL))
    if not admin:
        return False
    tokens = (await session.execute(select(DeviceToken).where(DeviceToken.user_id == admin.id))).scalars().all()
    sent = False
    for device in tokens:
        try:
            messaging.send(messaging.Message(
                data={
                    "title": title,
                    "body": body,
                    "url": settings.ADMIN_REVIEW_URL,
                    "channel_id": "admin_alerts",
                },
                android=messaging.AndroidConfig(priority="high"),
                token=device.fcm_token,
            ), app=app)
            sent = True
        except Exception:
            # Same dead-token/permission failures scripts/send_notifications.py
            # tolerates — one admin device failing shouldn't block the others.
            continue
    return sent
