"""
One-off migration: add notification_log.sent_date and the two partial unique
indexes that make notification dedup a database guarantee.

WHY. send_notifications.py checked "have we already sent this?" and then
sent, with a session-scoped advisory lock as the only thing serialising two
concurrent runs. That lock stopped excluding anything when DATABASE_URL moved
to a transaction-mode pooler on 2026-09-03 — the backend is returned to the
pool at every commit, so a second client can acquire a "held" lock (verified
in production). With no constraint behind the check, two runs could both pass
it and both push. news-notify.timer was live on both droplets at the time.

The indexes mirror the two dedup rules exactly:
    breaking -> at most one notification per (user, cluster), ever
    daily    -> at most one per (user, slot, UTC day)

sent_date exists because the daily index needs a day value and
date(timestamptz) is STABLE, not IMMUTABLE, so Postgres refuses to index it.

Existing duplicates are removed first (keeping the earliest row of each
group), otherwise CREATE UNIQUE INDEX fails. This is safe: the rows are a
send log, and a duplicate row means a notification that was already sent
twice — collapsing it loses no information the app acts on.

Idempotent. Usage:
    python3 scripts/add_notification_log_unique_indexes.py
    python3 scripts/add_notification_log_unique_indexes.py --dry-run
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import text

from app.database import admin_engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Keep the earliest row per group; ctid is the physical row id, which is what
# lets us delete duplicates without a unique key to join on.
DEDUP_BREAKING = """
DELETE FROM notification_log a USING notification_log b
WHERE a.mode = 'breaking' AND b.mode = 'breaking'
  AND a.user_id = b.user_id AND a.cluster_id = b.cluster_id
  AND a.ctid > b.ctid
"""

DEDUP_DAILY = """
DELETE FROM notification_log a USING notification_log b
WHERE a.mode = 'daily' AND b.mode = 'daily'
  AND a.user_id = b.user_id
  AND a.daily_slot_utc IS NOT DISTINCT FROM b.daily_slot_utc
  AND a.sent_date IS NOT DISTINCT FROM b.sent_date
  AND a.ctid > b.ctid
"""


async def main(dry_run: bool) -> None:
    engine = admin_engine()
    try:
        async with engine.begin() as conn:
            await conn.execute(text(
                "ALTER TABLE notification_log ADD COLUMN IF NOT EXISTS sent_date DATE"
            ))
            # Backfill in UTC to match send_notifications.py's today_start,
            # which is a UTC midnight boundary.
            filled = await conn.execute(text(
                "UPDATE notification_log SET sent_date = (sent_at AT TIME ZONE 'UTC')::date "
                "WHERE sent_date IS NULL"
            ))
            logger.info(f"Backfilled sent_date on {filled.rowcount} row(s).")

            for label, sql in (("breaking", DEDUP_BREAKING), ("daily", DEDUP_DAILY)):
                result = await conn.execute(text(sql))
                logger.info(f"Removed {result.rowcount} duplicate {label} row(s).")

            await conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_notiflog_breaking_user_cluster "
                "ON notification_log (user_id, cluster_id) WHERE mode = 'breaking'"
            ))
            await conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_notiflog_daily_user_slot_day "
                "ON notification_log (user_id, daily_slot_utc, sent_date) WHERE mode = 'daily'"
            ))
            logger.info("Partial unique indexes present.")

            if dry_run:
                raise _Rollback
    except _Rollback:
        logger.info("Dry run — all changes rolled back.")
    finally:
        await engine.dispose()


class _Rollback(Exception):
    pass


if __name__ == "__main__":
    asyncio.run(main(dry_run="--dry-run" in sys.argv))
