"""
One-off migration for existing deployments: creates the `entity_stats`
table and adds `story_clusters.entity_boost`, for the feed ranking
redesign's piece 1 (global importance). See app/models.py's EntityStat
and StoryCluster.entity_boost, and app/services/poller.py for how both are
maintained going forward, and app/services/entity_graph.py for entity
canonicalization.

This is a shadow signal only — it does not change /clusters ordering.
Safe to run multiple times (IF NOT EXISTS).

Usage:
    python3 scripts/add_entity_stats_table.py
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
            CREATE TABLE IF NOT EXISTS entity_stats (
                entity_key VARCHAR(255) PRIMARY KEY,
                display_name VARCHAR(255) NOT NULL,
                mention_count_decayed FLOAT NOT NULL DEFAULT 0.0,
                baseline_rate FLOAT NOT NULL DEFAULT 0.0,
                last_mentioned_at TIMESTAMPTZ,
                updated_at TIMESTAMPTZ
            )
        """))
        logger.info("entity_stats table is present.")

        await conn.execute(text(
            "ALTER TABLE story_clusters ADD COLUMN IF NOT EXISTS entity_boost FLOAT NOT NULL DEFAULT 0.0"
        ))
        logger.info("story_clusters.entity_boost column is present.")


if __name__ == "__main__":
    asyncio.run(main())
