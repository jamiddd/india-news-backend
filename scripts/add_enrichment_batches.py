"""
One-off migration for existing deployments: adds
`story_clusters.last_enriched_at` and the `enrichment_batches` table used by
the Batch API enrichment path.

See app/models.py for what both mean and app/services/enrichment_batch.py for
how they're used; docs/multi-source-feed-plan.md §5.G for why.

The backfill seeds last_enriched_at from last_updated_at for clusters that
are already ai_enriched. That is an approximation — the real timestamp was
never recorded — and it is deliberately conservative in one direction: it
only ever marks a cluster as "has been enriched before", which routes its
next pass to the cheap batch queue. A cluster wrongly left NULL costs one
full-price synchronous call; a cluster wrongly marked would delay a story
entering the feed. The former is the safe error, so only rows with
ai_enriched = TRUE (a genuinely successful paid call — see
scripts/add_ai_enriched_column.py) are touched.

Safe to run multiple times (IF NOT EXISTS, and the backfill only touches
rows still NULL).

Usage:
    python3 scripts/add_enrichment_batches.py
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
            "ALTER TABLE story_clusters "
            "ADD COLUMN IF NOT EXISTS last_enriched_at TIMESTAMPTZ"
        ))
        logger.info("story_clusters.last_enriched_at is present.")

        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS enrichment_batches (
                id SERIAL PRIMARY KEY,
                batch_id VARCHAR(128) NOT NULL UNIQUE,
                status VARCHAR(16) NOT NULL DEFAULT 'in_progress',
                request_count INTEGER NOT NULL DEFAULT 0,
                succeeded_count INTEGER,
                errored_count INTEGER,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                reconciled_at TIMESTAMPTZ
            )
        """))
        # Every tick queries exactly one thing: which batches are still open.
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_enrichment_batches_open "
            "ON enrichment_batches (created_at) WHERE status = 'in_progress'"
        ))
        logger.info("enrichment_batches table + index are present.")

        result = await conn.execute(text("""
            UPDATE story_clusters
            SET last_enriched_at = last_updated_at
            WHERE last_enriched_at IS NULL
              AND ai_enriched IS TRUE
        """))
        logger.info(
            f"Backfilled last_enriched_at for {result.rowcount} "
            f"already-enriched clusters."
        )


if __name__ == "__main__":
    asyncio.run(main())
