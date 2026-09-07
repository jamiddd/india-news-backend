"""
One-off migration for existing deployments: creates the
`story_timeline_features` table for the Timeline/Context tab (see
app/models.py's StoryTimelineFeature and
scripts/test_timeline_narrative.py's prompt-iteration harness). Editorial
picks land here via the admin endpoints in app/main.py
(POST /admin/timelines/pick, /unpick); the generation script (unwritten as
of this migration) fills in the narrative columns and the algorithmic
fallback slots.

Safe to run multiple times (IF NOT EXISTS).

Usage:
    python3 scripts/add_story_timeline_features_table.py
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
            CREATE TABLE IF NOT EXISTS story_timeline_features (
                id SERIAL PRIMARY KEY,
                anchor_cluster_id INTEGER NOT NULL REFERENCES story_clusters(id) ON DELETE CASCADE,
                is_editorial_pick BOOLEAN NOT NULL DEFAULT false,
                anchor_label VARCHAR(255),
                title TEXT,
                context TEXT,
                beats JSON,
                cluster_ids JSON,
                coherent BOOLEAN,
                last_seen_in_top BOOLEAN NOT NULL DEFAULT true,
                narrative_generated_at TIMESTAMPTZ,
                picked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ
            )
        """))
        await conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_story_timeline_features_anchor_cluster_id "
            "ON story_timeline_features (anchor_cluster_id)"
        ))
        # Generation-cycle query: which picks need a fresh narrative, ranked
        # editorial-first. Mirrors read_events' index-per-actual-query
        # convention rather than indexing every column pre-emptively.
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_story_timeline_features_is_editorial_pick "
            "ON story_timeline_features (is_editorial_pick)"
        ))
        logger.info("story_timeline_features table + indexes are present.")


if __name__ == "__main__":
    asyncio.run(main())
