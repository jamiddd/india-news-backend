"""
One-off migration for existing deployments: replaces the composite
(provider, provider_uid) unique index on `users` with a single-column
unique index on provider_uid alone, to match the Firebase Auth migration
(one stable Firebase uid per account regardless of linked provider).

Needed because this project doesn't run real Alembic migrations — the app
only does Base.metadata.create_all on startup, which never alters existing
indexes. See scripts/add_content_column.py for the same pattern.

Before running against a database with real user rows, check for
provider_uid collisions the composite index wouldn't have caught:
    SELECT provider_uid, count(*) FROM users GROUP BY provider_uid HAVING count(*) > 1;
Not expected to matter for this deployment (confirmed no real users yet at
migration time), but the CREATE UNIQUE INDEX below will fail loudly if it
does — nothing here silently drops rows.

Usage:
    python3 scripts/migrate_users_provider_uid_index.py
"""
import asyncio
import logging
import os
import sys

# Ensure root of repo/backend is in sys.path (matches other scripts/ here)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import text

from app.database import engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main():
    async with engine.begin() as conn:
        await conn.execute(text("DROP INDEX IF EXISTS uq_users_provider_uid"))
        await conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_provider_uid ON users (provider_uid)"
        ))
    logger.info("users.provider_uid is now uniquely indexed (single-column).")


if __name__ == "__main__":
    asyncio.run(main())
