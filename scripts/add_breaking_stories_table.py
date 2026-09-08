"""One-off migration for existing deployments: creates the
`breaking_stories` table for the "Breaking" slot feature (see
app/models.py's BreakingStory and the 2026-09-08 planning session).

Safe to run multiple times (IF NOT EXISTS).

Usage:
    python3 scripts/add_breaking_stories_table.py
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
            CREATE TABLE IF NOT EXISTS breaking_stories (
                id SERIAL PRIMARY KEY,
                cluster_id INTEGER NOT NULL UNIQUE REFERENCES story_clusters(id) ON DELETE CASCADE,
                status VARCHAR(16) NOT NULL DEFAULT 'active',
                title TEXT,
                beats JSON,
                promoted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                last_beat_at TIMESTAMPTZ,
                last_generated_at TIMESTAMPTZ,
                last_generated_source_count INTEGER,
                sources_at_promotion INTEGER NOT NULL,
                hours_to_threshold DOUBLE PRECISION NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        # Detector query filters "is there already an active/rejected row
        # for this cluster" and "how many rows are active right now" every
        # poll cycle — both want an index on status.
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_breaking_stories_status ON breaking_stories (status)"
        ))
        logger.info("breaking_stories table ready.")


if __name__ == "__main__":
    asyncio.run(main())
