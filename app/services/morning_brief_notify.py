"""Morning Brief push notification: once daily, tell opted-in users today's
Daily Brief (see app/services/daily_brief.py) is ready, deep-linking to the
Brief page of the Context tab rather than any single story.

Driven from scripts/send_notifications.py, alongside story_updates/
topic_updates/source_updates — same job lease, same FCM sender, own opt-in
(UserPreferences.morning_brief_notifications_enabled) and own NotificationLog
mode, so it dedups independently of every other push type.

Not tied to a narrow cron-aligned time window the way the "daily" mode's
per-user daily_notification_times_utc is: this fires on the first run of the
day where "now" is at or past SEND_AFTER_HOUR_IST:SEND_AFTER_MINUTE_IST
(matching the brief's ~05:00 IST build time with slack) AND today's brief is
ready AND this user hasn't been sent one yet today. That self-heals a late
brief build — worst case it just fires on whichever ~15-min
send_notifications.py run first sees a ready brief after the cutoff — and the
per-user "already sent today" check means there is no upper bound to widen
without risking a double-send.

NotificationLog.cluster_id is NOT NULL (see its docstring in app/models.py),
and this push has no single story of its own to attach — it stores the
brief's lead item's cluster_id purely so that FK is satisfied. The push
itself never deep-links there: it carries {"type": "morning_brief"} in its
data payload, which NewsFirebaseMessagingService.onMessageReceived checks
before ever looking at cluster_id, routing the tap straight to the Brief
page instead (see MainActivity.handleMorningBriefIntent).
"""
import logging
from datetime import datetime
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.orm import selectinload

from app.models import DailyBrief, NotificationLog, User

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")

MORNING_BRIEF_CHANNEL = "morning_brief"
MODE = "morning_brief"

# Local time-of-day the brief should be announced from, once it's ready.
SEND_AFTER_HOUR_IST = 6
SEND_AFTER_MINUTE_IST = 30

# (device_token, title, body, cluster_id, channel_id, extra) -> delivered?
SendFn = Callable[[str, str, str, int, str, dict], Awaitable[bool]]


async def send_morning_brief_notifications(session, now: datetime, send: SendFn) -> int:
    """Sends the "today's brief is ready" push to every opted-in user who
    hasn't had one yet today, once it's past the IST cutoff above and
    today's brief exists with status='ready'. No-ops before the cutoff or
    while the brief isn't ready yet — a later run the same day picks it up."""
    ist_now = now.astimezone(IST)
    if (ist_now.hour, ist_now.minute) < (SEND_AFTER_HOUR_IST, SEND_AFTER_MINUTE_IST):
        return 0

    brief = (
        await session.execute(
            select(DailyBrief).where(DailyBrief.brief_date == ist_now.date())
        )
    ).scalar_one_or_none()
    if brief is None or brief.status != "ready" or not brief.items:
        return 0
    hero_cluster_id = brief.items[0]["cluster_id"]

    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    result = await session.execute(
        select(User)
        .options(selectinload(User.device_tokens))
        .where(text("(preferences->>'morning_brief_notifications_enabled')::boolean IS TRUE"))
    )
    users = result.scalars().unique().all()

    sent = 0
    for user in users:
        if not user.device_tokens:
            continue

        already_sent = (
            await session.execute(
                select(NotificationLog.id).where(
                    NotificationLog.user_id == user.id,
                    NotificationLog.mode == MODE,
                    NotificationLog.sent_at >= today_start,
                )
            )
        ).scalar_one_or_none()
        if already_sent is not None:
            continue

        session.add(
            NotificationLog(
                user_id=user.id, cluster_id=hero_cluster_id, mode=MODE,
                sent_at=now, sent_date=now.date(),
            )
        )
        await session.commit()

        for device in list(user.device_tokens):
            ok = await send(
                device.fcm_token,
                "Morning Brief",
                "Today's top stories are ready.",
                hero_cluster_id,
                MORNING_BRIEF_CHANNEL,
                {"type": "morning_brief"},
            )
            if not ok:
                await session.delete(device)
        await session.commit()
        sent += 1

    return sent
