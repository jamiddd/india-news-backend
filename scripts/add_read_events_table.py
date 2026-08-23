"""
One-off migration for existing deployments: creates the `read_events` and
`user_entity_affinity` tables for the feed ranking redesign's piece 2
(per-user affinity / "For You" tab). See app/models.py's ReadEvent and
UserEntityAffinity, app/services/affinity.py, and the
POST /users/{user_id}/read-events + GET /clusters/for-you endpoints in
app/main.py.

Safe to run multiple times (IF NOT EXISTS).

Usage:
    python3 scripts/add_read_events_table.py
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
            CREATE TABLE IF NOT EXISTS read_events (
                id SERIAL PRIMARY KEY,
                user_id VARCHAR(64) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                cluster_id INTEGER NOT NULL REFERENCES story_clusters(id) ON DELETE CASCADE,
                event_id VARCHAR(64) NOT NULL,
                opened_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                dwell_ms INTEGER,
                scroll_depth_pct INTEGER,
                updated_at TIMESTAMPTZ
            )
        """))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_read_events_user_id ON read_events (user_id)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_read_events_cluster_id ON read_events (cluster_id)"
        ))
        # A real named UNIQUE CONSTRAINT, not just a unique index — the app's
        # upsert uses `ON CONFLICT ON CONSTRAINT uq_read_events_user_event`,
        # and Postgres only accepts that clause against an actual constraint
        # (pg_constraint entry), not a plain CREATE UNIQUE INDEX, even though
        # both enforce the same uniqueness. ADD CONSTRAINT has no IF NOT
        # EXISTS in Postgres, so guard it with a catalog check instead.
        await conn.execute(text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint WHERE conname = 'uq_read_events_user_event'
                ) THEN
                    ALTER TABLE read_events
                        ADD CONSTRAINT uq_read_events_user_event UNIQUE (user_id, event_id);
                END IF;
            END $$;
        """))
        logger.info("read_events table + indexes/constraint are present.")

        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS user_entity_affinity (
                user_id VARCHAR(64) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                entity_key VARCHAR(255) NOT NULL,
                affinity_decayed FLOAT NOT NULL DEFAULT 0.0,
                updated_at TIMESTAMPTZ,
                PRIMARY KEY (user_id, entity_key)
            )
        """))
        logger.info("user_entity_affinity table is present.")


if __name__ == "__main__":
    asyncio.run(main())
