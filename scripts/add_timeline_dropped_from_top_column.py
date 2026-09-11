"""One-off migration for existing deployments: adds dropped_from_top_at to
story_timeline_features (see app/models.py's StoryTimelineFeature). Drives
the Context tab's "Past stories" archive section and its 30-day cutoff.

Backfills existing last_seen_in_top=False rows from updated_at as a
best-effort approximation of when each one dropped (updated_at is bumped by
generate_for_chain even on a merely-re-rejected row, so this can be later
than the true drop for a row re-attempted after it first fell out — but it's
the only signal available for rows dropped before this column existed).

Safe to run multiple times (ADD COLUMN IF NOT EXISTS; backfill only touches
rows still NULL).

Usage:
    python3 scripts/add_timeline_dropped_from_top_column.py
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
            "ALTER TABLE story_timeline_features ADD COLUMN IF NOT EXISTS dropped_from_top_at TIMESTAMPTZ"
        ))
        result = await conn.execute(text(
            """
            UPDATE story_timeline_features
            SET dropped_from_top_at = updated_at
            WHERE last_seen_in_top = false AND dropped_from_top_at IS NULL
            """
        ))
        logger.info("backfilled dropped_from_top_at for %d row(s)", result.rowcount)


if __name__ == "__main__":
    asyncio.run(main())
