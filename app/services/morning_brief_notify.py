"""Brief-readiness push notifications: once daily, tell opted-in users that
today's Daily Brief (~05:00 IST) or tonight's Late-Night Wrap-up (~19:30 IST)
is ready, deep-linking to the Brief page of the Context tab rather than any
single story. Both are built by app/services/daily_brief.py, one DailyBrief
row per (brief_date, kind).

Driven from scripts/send_notifications.py, alongside story_updates/
topic_updates/source_updates — same job lease, same FCM sender. The two kinds
are siblings, not one push with two wordings: each has its own opt-in
(UserPreferences.morning_brief_notifications_enabled /
late_night_wrapup_notifications_enabled), its own NotificationLog mode and its
own channel, so a reader can take the morning one and skip the night one (or
mute either in Android's own settings) and neither dedups the other.

Not tied to a narrow cron-aligned time window the way the "daily" mode's
per-user daily_notification_times_utc is: each fires on the first run of the
day where "now" is at or past its own SEND_AFTER (with slack over its build
time) AND that kind's brief for today is ready AND this user hasn't been sent
one yet today. That self-heals a late build — worst case it just fires on
whichever ~15-min send_notifications.py run first sees a ready brief after the
cutoff — and the per-user "already sent today" check means there is no upper
bound to widen without risking a double-send. Neither needs an explicit upper
bound for its own sake: once the IST date rolls over, that date has no brief
yet, so the query finds nothing.

NotificationLog.cluster_id is NOT NULL (see its docstring in app/models.py),
and these pushes have no single story of their own to attach — they store the
brief's lead item's cluster_id purely so that FK is satisfied. The push itself
never deep-links there: it carries its own "type" in the data payload, which
NewsFirebaseMessagingService.onMessageReceived checks before ever looking at
cluster_id, routing the tap straight to the Brief page instead (see
MainActivity.handleMorningBriefIntent). At night that page shows the Wrap-up
(see the app's 8 PM-5 AM IST switch), which is why the Wrap-up push waits for
20:00 IST rather than firing at its 19:30 build.
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

WRAPUP_CHANNEL = "late_night_wrapup"
WRAPUP_MODE = "late_night_wrapup"

# Local time-of-day each kind is announced from, once it's ready. The Brief
# builds at ~05:00 IST; the Wrap-up builds at 19:30 IST but waits for 20:00,
# when the app's Brief page starts calling itself the Wrap-up.
SEND_AFTER_HOUR_IST = 6
SEND_AFTER_MINUTE_IST = 30
WRAPUP_SEND_AFTER_HOUR_IST = 20
WRAPUP_SEND_AFTER_MINUTE_IST = 0

# (device_token, title, body, cluster_id, channel_id, extra) -> delivered?
SendFn = Callable[[str, str, str, int, str, dict], Awaitable[bool]]


async def _send_brief_notifications(
    session,
    now: datetime,
    send: SendFn,
    *,
    kind: str,
    mode: str,
    channel: str,
    push_type: str,
    pref_key: str,
    after: tuple[int, int],
    title: str,
    body: str,
) -> int:
    """Shared body of the two pushes below — see the module docstring for the
    contract. Everything that differs between the morning Brief and the
    Late-Night Wrap-up is an argument here, so neither can drift from the
    other's dedup or opt-in behaviour."""
    ist_now = now.astimezone(IST)
    if (ist_now.hour, ist_now.minute) < after:
        return 0

    brief = (
        await session.execute(
            select(DailyBrief).where(
                DailyBrief.brief_date == ist_now.date(),
                # Without this there are two rows per date (brief + wrapup)
                # and scalar_one_or_none() raises MultipleResultsFound.
                DailyBrief.kind == kind,
            )
        )
    ).scalar_one_or_none()
    if brief is None or brief.status != "ready" or not brief.items:
        return 0
    hero_cluster_id = brief.items[0]["cluster_id"]

    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    result = await session.execute(
        select(User)
        .options(selectinload(User.device_tokens))
        .where(text(f"(preferences->>'{pref_key}')::boolean IS TRUE"))
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
                    NotificationLog.mode == mode,
                    NotificationLog.sent_at >= today_start,
                )
            )
        ).scalar_one_or_none()
        if already_sent is not None:
            continue

        session.add(
            NotificationLog(
                user_id=user.id, cluster_id=hero_cluster_id, mode=mode,
                sent_at=now, sent_date=now.date(),
            )
        )
        await session.commit()

        for device in list(user.device_tokens):
            ok = await send(
                device.fcm_token,
                title,
                body,
                hero_cluster_id,
                channel,
                {"type": push_type},
            )
            if not ok:
                await session.delete(device)
        await session.commit()
        sent += 1

    return sent


async def send_morning_brief_notifications(session, now: datetime, send: SendFn) -> int:
    """The morning "today's brief is ready" push, from 06:30 IST."""
    return await _send_brief_notifications(
        session, now, send,
        kind="brief",
        mode=MODE,
        channel=MORNING_BRIEF_CHANNEL,
        push_type="morning_brief",
        pref_key="morning_brief_notifications_enabled",
        after=(SEND_AFTER_HOUR_IST, SEND_AFTER_MINUTE_IST),
        title="Morning Brief",
        body="Today's top stories are ready.",
    )


async def send_wrapup_notifications(session, now: datetime, send: SendFn) -> int:
    """The night "tonight's wrap-up is ready" push, from 20:00 IST."""
    return await _send_brief_notifications(
        session, now, send,
        kind="wrapup",
        mode=WRAPUP_MODE,
        channel=WRAPUP_CHANNEL,
        push_type="late_night_wrapup",
        pref_key="late_night_wrapup_notifications_enabled",
        after=(WRAPUP_SEND_AFTER_HOUR_IST, WRAPUP_SEND_AFTER_MINUTE_IST),
        title="Late-Night Wrap-up",
        body="Tonight's top stories are ready.",
    )
