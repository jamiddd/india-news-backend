"""
One-off migration for existing deployments: adds the `distinct_source_count`
and `headline_score` columns (+ supporting index) to `story_clusters`, and
backfills both for every existing row. See app/models.py's StoryCluster for
what these mean and app/services/poller.py for how they're maintained going
forward.

Safe to run multiple times (IF NOT EXISTS / plain re-computation).

Usage:
    python3 scripts/add_ranking_columns.py
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
            "ALTER TABLE story_clusters ADD COLUMN IF NOT EXISTS distinct_source_count INTEGER NOT NULL DEFAULT 1"
        ))
        await conn.execute(text(
            "ALTER TABLE story_clusters ADD COLUMN IF NOT EXISTS headline_score FLOAT NOT NULL DEFAULT 0.0"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_clusters_headline_score "
            "ON story_clusters (headline_score DESC, id DESC)"
        ))
        logger.info("story_clusters.distinct_source_count / headline_score columns + index are present.")

        # Backfill distinct_source_count from actual article/source data —
        # article_count can't be reused here, it isn't source-deduped.
        result = await conn.execute(text("""
            UPDATE story_clusters sc SET distinct_source_count = sub.cnt
            FROM (
                SELECT cluster_id, COUNT(DISTINCT source_id) AS cnt
                FROM articles
                WHERE cluster_id IS NOT NULL
                GROUP BY cluster_id
            ) sub
            WHERE sc.id = sub.cluster_id
        """))
        logger.info(f"Backfilled distinct_source_count for {result.rowcount} clusters.")

        # Initial headline_score computation so day-one isn't all-zero
        # before the next poll cycle recomputes it fresh (see poller.py).
        result = await conn.execute(text("""
            UPDATE story_clusters SET headline_score =
                distinct_source_count / POWER(
                    GREATEST(EXTRACT(EPOCH FROM (now() - last_updated_at)) / 3600.0, 0) + 2,
                    1.5
                )
        """))
        logger.info(f"Backfilled headline_score for {result.rowcount} clusters.")


if __name__ == "__main__":
    asyncio.run(main())
