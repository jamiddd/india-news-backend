"""
One-off migration for existing deployments: creates the `explore_exposures`
table and adds `story_clusters.explore_status`, for the feed ranking
redesign's piece 3 (explore-slot bandit). See app/models.py's
ExploreExposure and StoryCluster.explore_status, app/services/
explore_bandit.py, and GET /clusters' optional user_id param in
app/main.py.

Safe to run multiple times (IF NOT EXISTS).

Usage:
    python3 scripts/add_explore_bandit_tables.py
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
            CREATE TABLE IF NOT EXISTS explore_exposures (
                id SERIAL PRIMARY KEY,
                cluster_id INTEGER NOT NULL REFERENCES story_clusters(id) ON DELETE CASCADE,
                user_id VARCHAR(64) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                position INTEGER NOT NULL DEFAULT 2,
                exposed_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_explore_exposures_cluster_id ON explore_exposures (cluster_id)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_explore_exposures_user_id ON explore_exposures (user_id)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_explore_exposures_exposed_at ON explore_exposures (exposed_at)"
        ))
        logger.info("explore_exposures table + indexes are present.")

        await conn.execute(text(
            "ALTER TABLE story_clusters ADD COLUMN IF NOT EXISTS explore_status VARCHAR(16) NOT NULL DEFAULT 'pending'"
        ))
        logger.info("story_clusters.explore_status column is present.")


if __name__ == "__main__":
    asyncio.run(main())
