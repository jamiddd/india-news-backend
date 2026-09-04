"""Ensure today's crossword exists and pre-generate tomorrow at 23:55 IST.

Horoscopes run on their own clock and are deliberately NOT part of the 23:55
run: AstroJson rolls over on UTC, so tomorrow's IST date does not exist for it
until 05:30 IST. Verified against the live API on 2026-09-05 — today returned
12/12 signs and tomorrow 0/12 — so asking at 23:55 is twelve paid calls that
cannot succeed. They are fetched at 06:00 IST instead, and the 00:00-05:30
window is served from the previous day (see app/services/horoscope.py).
"""
import asyncio
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.database import AsyncSessionLocal, Base, engine
from app.services.crossword import get_or_create_puzzle
from app.services.sudoku import get_or_create_sudoku
from app.services.word_search import get_or_create_word_search
from app.services.daily_games import get_or_create_daily_games
from app.services.editorial_features import get_or_create_editorial
from app.services.horoscope import prewarm_horoscopes
from sqlalchemy import text

IST = ZoneInfo("Asia/Kolkata")


async def ensure(day):
    async with AsyncSessionLocal() as session:
        # The one place an algorithmic puzzle is re-attempted against APIVerve
        # for better clues — once a night, never from a user request.
        puzzle = await get_or_create_puzzle(session, day, allow_upgrade=True)
        print(f"Crossword ready for {day} ({puzzle.source})", flush=True)
    async with AsyncSessionLocal() as session:
        await get_or_create_sudoku(session, day)
        print(f"Sudoku ready for {day}", flush=True)
    async with AsyncSessionLocal() as session:
        await get_or_create_word_search(session, day)
        print(f"Word search ready for {day}", flush=True)
    async with AsyncSessionLocal() as session:
        await get_or_create_daily_games(session, day)
        print(f"Spelling bee, word ladder, and quiz ready for {day}", flush=True)
    async with AsyncSessionLocal() as session:
        await get_or_create_editorial(session, day)
        print(f"Daily editorial features ready for {day}", flush=True)


async def horoscope_loop():
    """Top up today's horoscopes at 06:00 IST, after the provider's UTC rollover.

    This is the only place horoscopes are fetched on a schedule; the 00:00-05:30
    IST window before it is served from the previous day's forecast.
    """
    while True:
        now = datetime.now(IST)
        run_at = datetime.combine(now.date(), time(6, 0), tzinfo=IST)
        if now >= run_at:
            run_at += timedelta(days=1)
        await asyncio.sleep(max(1, (run_at - now).total_seconds()))
        try:
            day = datetime.now(IST).date()
            ready, total = await prewarm_horoscopes(AsyncSessionLocal, day)
            print(f"Horoscope top-up for {day}: {ready}/{total}", flush=True)
        except Exception as exc:  # noqa: BLE001 - never take the scheduler down
            print(f"Horoscope top-up failed: {exc}", flush=True)


async def puzzle_loop():
    while True:
        now = datetime.now(IST)
        run_at = datetime.combine(now.date(), time(23, 55), tzinfo=IST)
        if now >= run_at:
            run_at += timedelta(days=1)
        await asyncio.sleep(max(1, (run_at - now).total_seconds()))
        await ensure((run_at + timedelta(minutes=5)).date())


async def main():
    async with engine.begin() as connection:
        await connection.execute(text("SELECT pg_advisory_lock(918273645)"))
        try:
            await connection.run_sync(Base.metadata.create_all)
        finally:
            await connection.execute(text("SELECT pg_advisory_unlock(918273645)"))
    today = datetime.now(IST).date()
    await ensure(today)
    # Startup catch-up: a container that comes up after 06:00 IST would
    # otherwise wait until tomorrow for its first horoscope fetch.
    if datetime.now(IST).time() >= time(6, 0):
        ready, total = await prewarm_horoscopes(AsyncSessionLocal, today)
        print(f"Horoscopes ready for {today}: {ready}/{total}", flush=True)
    await asyncio.gather(puzzle_loop(), horoscope_loop())


if __name__ == "__main__":
    asyncio.run(main())
