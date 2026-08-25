"""
One-off migration for existing deployments: creates the `saved_stories`
table so bookmarked stories sync across a logged-in user's devices instead
of staying in local SharedPreferences. See app/models.py's SavedStory and
the POST/DELETE/GET /users/{user_id}/saved-stories endpoints in app/main.py.

Safe to run multiple times (IF NOT EXISTS).

Usage:
    python3 scripts/add_saved_stories_table.py
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
            CREATE TABLE IF NOT EXISTS saved_stories (
                id SERIAL PRIMARY KEY,
                user_id VARCHAR(64) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                cluster_id INTEGER NOT NULL REFERENCES story_clusters(id) ON DELETE CASCADE,
                saved_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_saved_stories_user_id ON saved_stories (user_id)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_saved_stories_cluster_id ON saved_stories (cluster_id)"
        ))
        await conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_saved_stories_user_cluster "
            "ON saved_stories (user_id, cluster_id)"
        ))
        logger.info("saved_stories table + indexes are present.")


if __name__ == "__main__":
    asyncio.run(main())
