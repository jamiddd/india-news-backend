"""
One-off migration for existing deployments: creates the `device_tokens` and
`notification_log` tables (+ supporting indexes) used by the push
notification feature. See app/models.py's DeviceToken/NotificationLog for
what these mean and scripts/send_notifications.py for how they're used.

Safe to run multiple times (IF NOT EXISTS throughout). No backfill needed —
both tables start empty on a fresh deploy.

Usage:
    python3 scripts/add_notification_tables.py
"""
import asyncio
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
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS device_tokens (
                id SERIAL PRIMARY KEY,
                user_id VARCHAR(64) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                fcm_token VARCHAR(512) NOT NULL,
                platform VARCHAR(20) DEFAULT 'android',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_device_tokens_user_id ON device_tokens (user_id)"
        ))
        await conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_device_tokens_fcm_token ON device_tokens (fcm_token)"
        ))
        logger.info("device_tokens table + indexes are present.")

        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS notification_log (
                id SERIAL PRIMARY KEY,
                user_id VARCHAR(64) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                cluster_id INTEGER NOT NULL REFERENCES story_clusters(id) ON DELETE CASCADE,
                mode VARCHAR(20) NOT NULL,
                sent_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_notiflog_user_sent ON notification_log (user_id, sent_at)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_notiflog_user_cluster ON notification_log (user_id, cluster_id)"
        ))
        logger.info("notification_log table + indexes are present.")


if __name__ == "__main__":
    asyncio.run(main())
