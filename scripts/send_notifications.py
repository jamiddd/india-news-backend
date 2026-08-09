"""
Decides who gets a push notification this run and sends it, for the two
opt-in modes users can pick in Settings:

- "breaking": headline_score > BREAKING_SCORE_THRESHOLD (0.4). This value is
  deliberately above 0.3536, the highest score a singleton (1-outlet) story
  can ever reach regardless of recency (score = distinct_source_count /
  (hours+2)^1.5, and a 1-source story at age 0 scores 1/2^1.5 = 0.3536) — so
  crossing 0.4 is only mathematically possible when 2+ independent outlets
  are actively corroborating the same story right now. Capped at
  BREAKING_DAILY_CAP (5) sends/day per user even on an unusually newsy day;
  confirmed against live production data that ~3 clusters/day naturally
  cross 0.4, so the cap is a rarely-binding safety net, not the normal case.
- "daily": one notification/day, at approximately the user's chosen
  preferred time (notification_time_utc, already UTC — see
  UserPreferences.notification_time_utc in schemas.py/NewsModels.kt for why
  no timezone field is needed), covering only the single top-headline_score
  cluster overall (not personalized).

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

from sqlalchemy import select, func, text
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

            # Users with notifications on and at least one registered device.
            result = await session.execute(
                select(User)
                .options(selectinload(User.device_tokens))
                .where(text("preferences->>'notification_frequency' != 'off'"))
            )
            users = result.scalars().unique().all()

            breaking_sent = 0
            daily_sent = 0

            for user in users:
                if not user.device_tokens:
                    continue
                frequency = (user.preferences or {}).get("notification_frequency", "off")

                if frequency == "breaking":
                    cap_used = (
                        await session.execute(
                            select(func.count(NotificationLog.id)).where(
                                NotificationLog.user_id == user.id,
                                NotificationLog.mode == "breaking",
                                NotificationLog.sent_at >= today_start,
                            )
                        )
                    ).scalar() or 0
                    if cap_used >= BREAKING_DAILY_CAP:
                        continue

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
                    if candidate is None:
                        continue

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
                    session.add(NotificationLog(user_id=user.id, cluster_id=candidate.id, mode="breaking"))
                    breaking_sent += 1

                elif frequency == "daily":
                    preferred_time = (user.preferences or {}).get("notification_time_utc")
                    if not preferred_time or not _is_within_daily_window(preferred_time, now):
                        continue

                    already_sent_today = (
                        await session.execute(
                            select(NotificationLog.id).where(
                                NotificationLog.user_id == user.id,
                                NotificationLog.mode == "daily",
                                NotificationLog.sent_at >= today_start,
                            )
                        )
                    ).scalar_one_or_none()
                    if already_sent_today is not None:
                        continue

                    top_story = (
                        await session.execute(
                            select(StoryCluster).order_by(StoryCluster.headline_score.desc()).limit(1)
                        )
                    ).scalar_one_or_none()
                    if top_story is None:
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
                    session.add(NotificationLog(user_id=user.id, cluster_id=top_story.id, mode="daily"))
                    daily_sent += 1

            await session.commit()
            logger.info(f"[Notifications sent] breaking={breaking_sent} daily={daily_sent} (users checked={len(users)})")
        finally:
            await session.rollback()
            await session.execute(select(func.pg_advisory_unlock(NOTIFY_LOCK_KEY)))
            await session.commit()


if __name__ == "__main__":
    asyncio.run(main())
