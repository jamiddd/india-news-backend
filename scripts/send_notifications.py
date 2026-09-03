"""
Decides who gets a push notification this run and sends it. Breaking alerts
and daily digests are independent opt-ins — a user can have both on at once
(see UserPreferences.breaking_notifications_enabled /
daily_notification_times_utc in schemas.py/NewsModels.kt):

- Breaking: headline_score > BREAKING_SCORE_THRESHOLD (0.4). This value is
  deliberately above 0.3536, the highest score a singleton (1-outlet) story
  can ever reach regardless of recency (score = distinct_source_count /
  (hours+2)^1.5, and a 1-source story at age 0 scores 1/2^1.5 = 0.3536) — so
  crossing 0.4 is only mathematically possible when 2+ independent outlets
  are actively corroborating the same story right now. Capped at
  BREAKING_DAILY_CAP (5) sends/day per user even on an unusually newsy day;
  confirmed against live production data that ~3 clusters/day naturally
  cross 0.4, so the cap is a rarely-binding safety net, not the normal case.
- Daily: one notification per configured time-of-day (a user can pick
  several), covering only the single top-headline_score cluster overall
  (not personalized). Dedup is per time-slot via NotificationLog.
  daily_slot_utc, not just "already sent today", so multiple
  daily_notification_times_utc entries each get their own send.

Meant to run every ~15 min via cron, a few minutes after poll_all_sources()
recomputes headline_score (see poller.py) — e.g. :05/:20/:35/:50, following
poll's :00/:15/:30/:45. Structured like enrich_all_clusters.py: its own
AsyncSessionLocal, capped batch size, safe to invoke via `docker exec`.

Usage:
    python3 scripts/send_notifications.py
"""
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select, func, text, or_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import selectinload

from firebase_admin import messaging

from app.database import AsyncSessionLocal
from app.models import User, DeviceToken, NotificationLog, StoryCluster, utc_now
from app.services.firebase_auth import _get_firebase_app

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# See module docstring for why this exact value.
BREAKING_SCORE_THRESHOLD = 0.4
BREAKING_DAILY_CAP = 5

# Job runs every ~15 min; treat a user as "at their preferred time" if we're
# within half that cadence of it, so nobody is skipped just because the job's
# exact minute doesn't land on theirs, but also isn't double-fired by two
# consecutive runs both falling in range.
DAILY_WINDOW_MINUTES = 7

# Arbitrary fixed key for this job's Postgres advisory lock — same pattern as
# POLL_LOCK_KEY in poller.py, guarding against overlapping cron runs
# double-sending if one invocation runs long.
NOTIFY_LOCK_KEY = 872459124


def _minutes_since_midnight(hhmm: str) -> int:
    hour, minute = hhmm.split(":")
    return int(hour) * 60 + int(minute)


def _is_within_daily_window(notification_time_utc: str, now: datetime) -> bool:
    target = _minutes_since_midnight(notification_time_utc)
    current = now.hour * 60 + now.minute
    # Circular distance so a preferred time near midnight (e.g. 23:58)
    # doesn't wrongly appear ~1440 minutes away from 00:02.
    diff = abs(current - target)
    diff = min(diff, 1440 - diff)
    return diff <= DAILY_WINDOW_MINUTES


def _due_daily_slots(times_utc: list, now: datetime) -> list:
    """Which of a user's configured daily_notification_times_utc entries are
    due this run (within DAILY_WINDOW_MINUTES of now). A user with several
    times can have more than one come due on the same run in principle
    (only if two picks are within the cadence of each other), each handled
    as its own send/dedup below."""
    return [t for t in times_utc if _is_within_daily_window(t, now)]


async def _send(app, token: str, title: str, body: str, cluster_id: int, channel_id: str) -> bool:
    """Sends one message; returns False (and deletes the token row) if FCM
    reports it as dead, so future runs stop paying the cost of trying it.

    Deliberately data-only (no top-level `notification` field). Confirmed
    live against a real device: with a `notification` field present, FCM
    auto-displays the tray notification itself whenever the app is
    backgrounded/killed, WITHOUT calling onMessageReceived() at all — our
    NewsFirebaseMessagingService's custom PendingIntent (carrying cluster_id
    as a typed Int extra) never gets attached. Instead the OS's own
    fallback launch intent carries the data map's values as raw Strings,
    which MainActivity.getIntExtra(..., Int) can't read (crashes to the
    -1 default, so tapping the notification just opens the app with no
    deep-link). Data-only messages route through onMessageReceived() in
    every app state (foreground and background alike), so our own code is
    always the one building the notification and its PendingIntent."""
    message = messaging.Message(
        data={
            "title": title,
            "body": body,
            "cluster_id": str(cluster_id),
            "channel_id": channel_id,
        },
        android=messaging.AndroidConfig(priority="high"),
        token=token,
    )
    try:
        messaging.send(message, app=app)
        return True
    except Exception as e:
        # firebase_admin raises different exception types across versions
        # for "this token is dead" (UnregisteredError / NotFoundError /
        # InvalidArgumentError depending on SDK version) — match on the
        # exception class name rather than importing a specific one, so this
        # keeps working across firebase-admin upgrades.
        exc_name = type(e).__name__
        if any(marker in exc_name for marker in ("Unregistered", "NotFound", "InvalidArgument")):
            logger.info(f"[Dead token] {exc_name} for token ...{token[-8:]} — will be deleted")
        else:
            logger.warning(f"[Send failed] {exc_name}: {e}")
        return False


async def _claim(session, **fields) -> bool:
    """Atomically claim the right to send one notification.

    Returns True only if THIS caller inserted the row. The partial unique
    indexes on notification_log (see app/models.py) turn a duplicate into a
    no-op via ON CONFLICT, so two concurrent runs cannot both claim the same
    (user, cluster) breaking send or the same (user, slot, day) daily send.

    Claim first, then push. The previous order — check, push, log, commit
    everything at the end — was a check-then-act race whose only serialisation
    was a session-scoped advisory lock, and that lock silently stopped
    excluding anything under transaction-mode pooling. Claiming first can at
    worst drop a notification if the push then fails; the old order could send
    the same push twice. For push notifications that trade is the right way
    round, and it matches the existing behaviour of logging the send even when
    a device token turns out to be dead.

    Committed immediately and independently: an uncommitted claim is invisible
    to the other runner and would defeat the whole point.
    """
    result = await session.execute(
        pg_insert(NotificationLog)
        .values(**fields)
        .on_conflict_do_nothing()
    )
    await session.commit()
    return result.rowcount > 0


async def main():
    async with AsyncSessionLocal() as session:
        got_lock = (await session.execute(select(func.pg_try_advisory_lock(NOTIFY_LOCK_KEY)))).scalar()
        if not got_lock:
            logger.warning("Skipping: another send_notifications run is already in progress.")
            return

        try:
            app = _get_firebase_app()
        except RuntimeError as e:
            logger.error(f"Firebase not configured, aborting: {e}")
            await session.execute(select(func.pg_advisory_unlock(NOTIFY_LOCK_KEY)))
            return

        try:
            now = datetime.now(timezone.utc)
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

            # Users with either notification mode on and at least one
            # registered device. Breaking and daily are independent opt-ins
            # now, so this OR's both flags rather than checking one string.
            result = await session.execute(
                select(User)
                .options(selectinload(User.device_tokens))
                .where(
                    or_(
                        text("(preferences->>'breaking_notifications_enabled')::boolean IS TRUE"),
                        # preferences is a `json` column, not `jsonb` — jsonb_array_length
                        # needs an explicit cast, json/jsonb aren't binary-compatible.
                        text("jsonb_array_length(COALESCE((preferences->'daily_notification_times_utc')::jsonb, '[]'::jsonb)) > 0"),
                    )
                )
            )
            users = result.scalars().unique().all()

            breaking_sent = 0
            daily_sent = 0
            top_story = None  # lazily fetched once, shared across all daily sends this run

            for user in users:
                if not user.device_tokens:
                    continue
                prefs = user.preferences or {}

                if prefs.get("breaking_notifications_enabled"):
                    cap_used = (
                        await session.execute(
                            select(func.count(NotificationLog.id)).where(
                                NotificationLog.user_id == user.id,
                                NotificationLog.mode == "breaking",
                                NotificationLog.sent_at >= today_start,
                            )
                        )
                    ).scalar() or 0
                    if cap_used < BREAKING_DAILY_CAP:
                        already_notified = select(NotificationLog.cluster_id).where(
                            NotificationLog.user_id == user.id, NotificationLog.mode == "breaking"
                        )
                        candidate = (
                            await session.execute(
                                select(StoryCluster)
                                .where(
                                    StoryCluster.headline_score > BREAKING_SCORE_THRESHOLD,
                                    StoryCluster.id.notin_(already_notified),
                                )
                                .order_by(StoryCluster.headline_score.desc())
                                .limit(1)
                            )
                        ).scalar_one_or_none()
                        if candidate is not None and await _claim(
                            session,
                            user_id=user.id,
                            cluster_id=candidate.id,
                            mode="breaking",
                            sent_at=now,
                            sent_date=now.date(),
                        ):
                            for device in list(user.device_tokens):
                                ok = await _send(
                                    app, device.fcm_token,
                                    title="Breaking",
                                    body=candidate.headline,
                                    cluster_id=candidate.id,
                                    channel_id="breaking_news",
                                )
                                if not ok:
                                    await session.delete(device)
                            breaking_sent += 1

                due_slots = _due_daily_slots(prefs.get("daily_notification_times_utc") or [], now)
                for slot in due_slots:
                    # Exact (user, slot, today) match rather than a sent_at
                    # time-window comparison — a window can't tell two
                    # configured times apart if they're close together, and
                    # needs today's-date arithmetic that breaks for a slot
                    # near midnight. daily_slot_utc sidesteps both.
                    already_sent_for_slot = (
                        await session.execute(
                            select(NotificationLog.id).where(
                                NotificationLog.user_id == user.id,
                                NotificationLog.mode == "daily",
                                NotificationLog.daily_slot_utc == slot,
                                NotificationLog.sent_at >= today_start,
                            )
                        )
                    ).scalar_one_or_none()
                    if already_sent_for_slot is not None:
                        # Fast path only — avoids fetching top_story for a
                        # slot already handled. _claim() below is what
                        # actually guarantees uniqueness.
                        continue

                    if top_story is None:
                        top_story = (
                            await session.execute(
                                select(StoryCluster).order_by(StoryCluster.headline_score.desc()).limit(1)
                            )
                        ).scalar_one_or_none()
                    if top_story is None:
                        continue

                    if not await _claim(
                        session,
                        user_id=user.id,
                        cluster_id=top_story.id,
                        mode="daily",
                        daily_slot_utc=slot,
                        sent_at=now,
                        sent_date=now.date(),
                    ):
                        continue

                    for device in list(user.device_tokens):
                        ok = await _send(
                            app, device.fcm_token,
                            title="Top story",
                            body=top_story.headline,
                            cluster_id=top_story.id,
                            channel_id="daily_digest",
                        )
                        if not ok:
                            await session.delete(device)
                    daily_sent += 1

            await session.commit()
            logger.info(f"[Notifications sent] breaking={breaking_sent} daily={daily_sent} (users checked={len(users)})")
        finally:
            await session.rollback()
            await session.execute(select(func.pg_advisory_unlock(NOTIFY_LOCK_KEY)))
            await session.commit()


if __name__ == "__main__":
    asyncio.run(main())
