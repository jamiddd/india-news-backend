"""Ensure today's crossword exists and pre-generate tomorrow at 23:55 IST."""
import asyncio
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.database import AsyncSessionLocal, Base, engine
from app.services.crossword import get_or_create_puzzle
from app.services.sudoku import get_or_create_sudoku
from sqlalchemy import text

IST = ZoneInfo("Asia/Kolkata")


async def ensure(day):
    async with AsyncSessionLocal() as session:
        puzzle = await get_or_create_puzzle(session, day)
        print(f"Crossword ready for {day} ({puzzle.source})", flush=True)
    async with AsyncSessionLocal() as session:
        await get_or_create_sudoku(session, day)
        print(f"Sudoku ready for {day}", flush=True)


async def main():
    async with engine.begin() as connection:
        await connection.execute(text("SELECT pg_advisory_lock(918273645)"))
        try:
            await connection.run_sync(Base.metadata.create_all)
        finally:
            await connection.execute(text("SELECT pg_advisory_unlock(918273645)"))
    await ensure(datetime.now(IST).date())

    while True:
        now = datetime.now(IST)
        run_at = datetime.combine(now.date(), time(23, 55), tzinfo=IST)
        if now >= run_at:
            run_at += timedelta(days=1)
        await asyncio.sleep(max(1, (run_at - now).total_seconds()))
        await ensure((run_at + timedelta(minutes=5)).date())


if __name__ == "__main__":
    asyncio.run(main())
