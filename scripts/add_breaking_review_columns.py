"""One-off migration for existing deployments: adds the human-review columns
to `breaking_stories` and creates `breaking_refresh_reviews` (see
app/models.py's BreakingStory/BreakingRefreshReview and
backend/docs/breaking-human-review-plan.md).

Safe to run multiple times (IF NOT EXISTS / ADD COLUMN IF NOT EXISTS).

Usage:
    python3 scripts/add_breaking_review_columns.py
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
        await conn.execute(text(
            "ALTER TABLE breaking_stories ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ"
        ))
        await conn.execute(text(
            "ALTER TABLE breaking_stories ADD COLUMN IF NOT EXISTS last_reviewed_source_count INTEGER"
        ))
        # Existing active rows already have a real last_generated_source_count
        # from the old auto-judge path — seed last_reviewed_source_count from
        # it so the first post-migration refresh check compares against where
        # generation actually left off, not NULL (which would read as "never
        # reviewed" and immediately re-flag every active row for review).
        await conn.execute(text(
            """
            UPDATE breaking_stories
            SET last_reviewed_source_count = last_generated_source_count
            WHERE status = 'active' AND last_reviewed_source_count IS NULL
            """
        ))

        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS breaking_refresh_reviews (
                id SERIAL PRIMARY KEY,
                cluster_id INTEGER NOT NULL REFERENCES story_clusters(id) ON DELETE CASCADE,
                status VARCHAR(16) NOT NULL DEFAULT 'pending',
                source_count_at_review INTEGER NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                reviewed_at TIMESTAMPTZ
            )
        """))
        # find_refresh_candidates needs "is there already a pending review for
        # this cluster" every poll cycle, and the admin page lists by status.
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_breaking_refresh_reviews_status "
            "ON breaking_refresh_reviews (status)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_breaking_refresh_reviews_cluster_id "
            "ON breaking_refresh_reviews (cluster_id)"
        ))
        logger.info("breaking_stories review columns + breaking_refresh_reviews table ready.")


if __name__ == "__main__":
    asyncio.run(main())
