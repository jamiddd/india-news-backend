"""
One-off migration for existing deployments: adds indexes for query paths
that were doing sequential scans on the hot list/poll endpoints — found
during an egress/efficiency audit (2026-08-28). None of these change
behavior, only how Postgres answers the same queries:

  - story_clusters.distinct_source_count — filtered by GET /clusters's
    `min_sources` param (the app's "Top Headlines" tab passes 2) and by
    explore_bandit.pick_candidate's CANDIDATE_MAX_SOURCES check.
  - story_clusters.explore_status — filtered by pick_candidate and
    recompute_explore_promotions (both app/services/explore_bandit.py).
  - sources.category — the join target of every category-tab subquery in
    GET /clusters (main.py).
  - articles (source_id, lower(title)) — poller.py's per-RSS-item
    "has this exact title already been ingested from this source"
    recurring-template check runs this on every item, every source, every
    poll cycle; previously unindexed.

Safe to run multiple times (IF NOT EXISTS).

Usage:
    python3 scripts/add_hot_path_indexes.py
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
            "CREATE INDEX IF NOT EXISTS idx_clusters_distinct_source_count "
            "ON story_clusters (distinct_source_count, headline_score DESC, id DESC)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_clusters_explore_status "
            "ON story_clusters (explore_status)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_sources_category ON sources (category)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_articles_source_lower_title "
            "ON articles (source_id, lower(title))"
        ))
        logger.info("Hot-path indexes are present.")


if __name__ == "__main__":
    asyncio.run(main())
