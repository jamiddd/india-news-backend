"""
One-off migration for existing deployments: adds the
`became_multi_source_at` column (+ supporting index) to `story_clusters` and
backfills it for rows that are already multi-source.

See app/models.py's StoryCluster for what the column means,
app/services/poller.py for how it's maintained going forward, and
docs/multi-source-feed-plan.md §5.B for why it exists.

The backfill uses first_seen_at, which is a deliberate approximation: the
real crossing moment wasn't recorded before this column existed, and
first_seen_at is the only never-rewritten timestamp on the row (see the
LISTING_MAX_AGE comment in app/main.py for why last_updated_at can't be
trusted here). Backfilled rows therefore behave exactly as they do today —
no row is aged out or reordered by this migration. Only clusters that cross
the threshold after deploy get a true crossing timestamp.

Safe to run multiple times (IF NOT EXISTS, and the backfill only touches
rows still NULL).

Usage:
    python3 scripts/add_became_multi_source_at.py
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import text

from app.config import settings
from app.database import engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main():
    threshold = settings.FEED_MIN_DISTINCT_SOURCES
    async with engine.begin() as conn:
        await conn.execute(text(
            "ALTER TABLE story_clusters "
            "ADD COLUMN IF NOT EXISTS became_multi_source_at TIMESTAMPTZ"
        ))
        # Partial index: the column is NULL for the ~92% of clusters that are
        # singletons, and every query that touches it is interested in the
        # rows where it is set.
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_clusters_became_multi_source_at "
            "ON story_clusters (became_multi_source_at DESC) "
            "WHERE became_multi_source_at IS NOT NULL"
        ))
        # Listings age-filter on COALESCE(became_multi_source_at,
        # first_seen_at) (see _listing_age_anchor in app/main.py), which the
        # plain first_seen_at index cannot serve — that filter needs a
        # matching expression index or it degrades to a seq scan.
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_clusters_listing_age_anchor "
            "ON story_clusters "
            "(COALESCE(became_multi_source_at, first_seen_at) DESC)"
        ))
        logger.info("story_clusters.became_multi_source_at column + indexes are present.")

        result = await conn.execute(
            text("""
                UPDATE story_clusters
                SET became_multi_source_at = first_seen_at
                WHERE became_multi_source_at IS NULL
                  AND distinct_source_count >= :threshold
            """),
            {"threshold": threshold},
        )
        logger.info(
            f"Backfilled became_multi_source_at (from first_seen_at) for "
            f"{result.rowcount} clusters at >= {threshold} distinct sources."
        )


if __name__ == "__main__":
    asyncio.run(main())
