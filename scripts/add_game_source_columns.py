"""
One-off migration for existing deployments: adds a `source` column
(default 'ai') to daily_word_searches, daily_spelling_bees,
daily_word_ladders and daily_quizzes, mirroring the column
daily_crosswords already had. Lets ops/admin tell apart days where the LLM
generation succeeded from days that fell back to the deterministic/curated
generator. See app/services/word_search.py and app/services/daily_games.py.

Safe to run multiple times (IF NOT EXISTS).

Usage:
    python3 scripts/add_game_source_columns.py
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

TABLES = [
    "daily_word_searches",
    "daily_spelling_bees",
    "daily_word_ladders",
    "daily_quizzes",
]


async def main():
    async with engine.begin() as conn:
        for table in TABLES:
            await conn.execute(text(
                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS source VARCHAR(30) NOT NULL DEFAULT 'ai'"
            ))
            logger.info("%s.source column is present.", table)


if __name__ == "__main__":
    asyncio.run(main())
