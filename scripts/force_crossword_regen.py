"""
One-off: force today's (or a given date's) crossword to regenerate right now
via get_or_create_puzzle, instead of waiting for the 23:55 IST scheduler run.
Useful after a prompt/validation fix to confirm it actually resolves to "ai"
without waiting for the next scheduled run. Mutates the DB (upserts the
crossword row for that date) — not read-only like the other scripts/ here.

Usage (inside the app or crossword_scheduler container):
    python3 scripts/force_crossword_regen.py [YYYY-MM-DD]
"""
import asyncio
import os
import sys
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import AsyncSessionLocal
from app.services.crossword import get_or_create_puzzle


async def main(target_date: date):
    async with AsyncSessionLocal() as session:
        puzzle = await get_or_create_puzzle(session, target_date)
        print(f"source: {puzzle.source}")


if __name__ == "__main__":
    target_date = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()
    asyncio.run(main(target_date))
