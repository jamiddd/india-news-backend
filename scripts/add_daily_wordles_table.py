"""
One-off migration for existing deployments: creates the `daily_wordles`
table backing the daily Wordle game (see app/models.py's DailyWordle and
GET /api/v1/wordle/daily).

Only the answer is stored. The accepted-guess list is the same ~6,400 words
every day and is re-read from app/data/wordlists/wordle_guesses.txt at serve
time, so it is deliberately not a column.

Safe to run multiple times (IF NOT EXISTS).

Usage:
    python3 scripts/add_daily_wordles_table.py
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
            CREATE TABLE IF NOT EXISTS daily_wordles (
                id SERIAL PRIMARY KEY,
                puzzle_date DATE NOT NULL UNIQUE,
                answer VARCHAR(10) NOT NULL,
                source VARCHAR(30) NOT NULL DEFAULT 'wordlist',
                generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_daily_wordles_puzzle_date ON daily_wordles (puzzle_date)"
        ))
        logger.info("daily_wordles table is present.")


if __name__ == "__main__":
    asyncio.run(main())
