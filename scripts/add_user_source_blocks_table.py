"""
One-off migration for existing deployments: creates the
`user_source_blocks` table backing "blocked sources" — a client-side-only
preference that hides a source's stories everywhere in the app (feed,
search, saved, related, notifications). See app/models.py's
UserSourceBlock and the block/unblock/list endpoints in app/main.py.

Safe to run multiple times (IF NOT EXISTS).

Usage:
    python3 scripts/add_user_source_blocks_table.py
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
            CREATE TABLE IF NOT EXISTS user_source_blocks (
                user_id VARCHAR(64) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (user_id, source_id)
            )
        """))
        logger.info("user_source_blocks table is present.")


if __name__ == "__main__":
    asyncio.run(main())
