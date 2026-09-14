"""One-off migration for existing deployments: adds the spoken-narration
audio columns to story_timeline_features (see app/models.py's
StoryTimelineFeature). No backfill — every existing row simply gets these
columns as NULL, which is the correct "no audio generated yet" state, and
the tab renders identically to before until the generation cycle fills them
in for a pick.

Safe to run multiple times (ADD COLUMN IF NOT EXISTS).

Usage:
    python3 scripts/add_timeline_audio_columns.py
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

COLUMNS = [
    ("spoken_script", "JSON"),
    ("spoken_script_hash", "VARCHAR(64)"),
    ("audio_url", "TEXT"),
    ("audio_duration_seconds", "INTEGER"),
    ("audio_beat_offsets", "JSON"),
    ("audio_generated_at", "TIMESTAMPTZ"),
]


async def main():
    async with engine.begin() as conn:
        for name, sql_type in COLUMNS:
            await conn.execute(text(
                f"ALTER TABLE story_timeline_features ADD COLUMN IF NOT EXISTS {name} {sql_type}"
            ))
            logger.info("ensured column story_timeline_features.%s (%s)", name, sql_type)


if __name__ == "__main__":
    asyncio.run(main())
