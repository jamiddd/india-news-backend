"""
One-off migration for existing deployments, needed for the breaking/daily
notification redesign: adds notification_log.daily_slot_utc (per-slot daily
dedup — see app/models.py's NotificationLog), and backfills any user whose
stored `preferences` JSON still has the old single-string
`notification_frequency`/`notification_time_utc` fields onto the new
independent `breaking_notifications_enabled`/`daily_notification_times_utc`
fields, then drops the old keys. Without this, a user who had
notification_frequency="daily" or "breaking" set would silently stop
receiving notifications after deploy (UserPreferences.model_validate just
ignores unknown old keys and defaults the new ones to off/empty).

Safe to run multiple times — the column add is IF NOT EXISTS, and the
preferences backfill only touches rows that still have the old keys (a
second run is a no-op).

Usage:
    python3 scripts/migrate_notification_prefs.py
"""
import asyncio
import json
import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import text

from app.database import engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main():
    async with engine.begin() as conn:
        await conn.execute(text(
            "ALTER TABLE notification_log ADD COLUMN IF NOT EXISTS daily_slot_utc VARCHAR(5)"
        ))
        logger.info("notification_log.daily_slot_utc is present.")

        # preferences is a `json` column, not `jsonb` — the `?` existence
        # operator only works on jsonb, so check via ->> instead.
        result = await conn.execute(text(
            "SELECT id, preferences FROM users WHERE preferences->>'notification_frequency' IS NOT NULL"
        ))
        rows = result.fetchall()
        for row in rows:
            user_id, raw_prefs = row[0], row[1]
            # A raw text() query doesn't get SQLAlchemy's JSON type result
            # processor, so asyncpg hands back the json column as a string,
            # not an already-decoded dict.
            prefs = json.loads(raw_prefs) if isinstance(raw_prefs, str) else dict(raw_prefs or {})
            frequency = prefs.pop("notification_frequency", "off")
            preferred_time = prefs.pop("notification_time_utc", None)

            prefs["breaking_notifications_enabled"] = (frequency == "breaking")
            prefs["daily_notification_times_utc"] = [preferred_time] if (frequency == "daily" and preferred_time) else []

            await conn.execute(
                text("UPDATE users SET preferences = CAST(:prefs AS json) WHERE id = :id"),
                {"prefs": json.dumps(prefs), "id": user_id},
            )
        logger.info(f"Backfilled notification preferences for {len(rows)} user(s).")


if __name__ == "__main__":
    asyncio.run(main())
