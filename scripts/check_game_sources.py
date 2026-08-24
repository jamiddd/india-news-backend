"""
One-off diagnostic: prints the `source` column (which generator produced the
puzzle — AI vs. a deterministic/curated fallback) for the most recent daily
games, across all 5 tables that track it. Read-only.

Source values per table (see app/services/daily_games.py, crossword.py,
word_search.py):
    daily_crosswords    -> "ai" | "algorithmic"
    daily_word_searches  -> "ai" | "curated"
    daily_spelling_bees   -> "ai" | "curated"
    daily_word_ladders    -> "ai" | "curated"
    daily_quizzes         -> "ai" | "curated"
  ("curated"/"algorithmic" means the LLM call failed, its output failed
  validation, or — for the quiz specifically — there weren't enough
  corroborated stories that day to prompt the LLM at all.)

Usage (inside the app container):
    python3 scripts/check_game_sources.py [days_back]
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import text

from app.database import engine

TABLES = [
    "daily_crosswords",
    "daily_word_searches",
    "daily_spelling_bees",
    "daily_word_ladders",
    "daily_quizzes",
]


async def main(days_back: int):
    async with engine.begin() as conn:
        for table in TABLES:
            result = await conn.execute(text(
                f"SELECT puzzle_date, source FROM {table} "
                f"ORDER BY puzzle_date DESC LIMIT :n"
            ), {"n": days_back})
            rows = result.all()
            print(f"\n{table}:")
            if not rows:
                print("  (no rows)")
            for row in rows:
                print(f"  {row.puzzle_date}\t{row.source}")


if __name__ == "__main__":
    days_back = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    asyncio.run(main(days_back))
