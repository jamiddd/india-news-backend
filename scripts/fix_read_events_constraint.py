"""
One-off fix for deployments that already ran the original
add_read_events_table.py: that version created a plain
`CREATE UNIQUE INDEX uq_read_events_user_event`, but the app's upsert uses
`ON CONFLICT ON CONSTRAINT uq_read_events_user_event`, which Postgres only
accepts against a real named constraint (pg_constraint entry) — a plain
unique index doesn't count, even though both enforce the same uniqueness.
Fails in prod with: asyncpg.exceptions.UndefinedObjectError: constraint
"uq_read_events_user_event" for table "read_events" does not exist.

Drops the old plain index (if present under that name) and adds a real
UNIQUE CONSTRAINT with the same name instead — same columns, same
uniqueness guarantee, just the right catalog object type. Safe to run
multiple times.

Usage:
    python3 scripts/fix_read_events_constraint.py
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
        has_constraint = (await conn.execute(text(
            "SELECT 1 FROM pg_constraint WHERE conname = 'uq_read_events_user_event'"
        ))).first()
        if has_constraint:
            logger.info("uq_read_events_user_event is already a real constraint — nothing to do.")
            return

        # DROP INDEX is a no-op error if it's not actually an index (e.g.
        # already a constraint's backing index) — IF EXISTS covers that.
        await conn.execute(text("DROP INDEX IF EXISTS uq_read_events_user_event"))
        await conn.execute(text(
            "ALTER TABLE read_events ADD CONSTRAINT uq_read_events_user_event UNIQUE (user_id, event_id)"
        ))
        logger.info("uq_read_events_user_event is now a real UNIQUE CONSTRAINT.")


if __name__ == "__main__":
    asyncio.run(main())
