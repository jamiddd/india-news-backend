"""
One-off: force today's (or a given date's) Spelling Bee, Word Ladder, and/or
Quiz to regenerate right now, instead of waiting for the next scheduler run.
Useful after a prompt/validation fix (see VALIDATION_RETRY_ATTEMPTS in
daily_games.py) to confirm it actually resolves to "ai" without waiting.

Unlike get_or_create_daily_games (used by the real request path), this does
NOT skip regeneration just because a row already exists for that date —
today's row may already exist with source="curated" from before the fix,
and get_or_create_daily_games treats any existing row as final regardless of
source. This script deletes the existing row (if any) for the requested
game(s) and inserts a fresh one. Mutates the DB — not read-only like the
other scripts/ here.

Usage (inside the app or crossword_scheduler container):
    python3 scripts/force_daily_games_regen.py [bee|ladder|quiz|all] [YYYY-MM-DD]
    python3 scripts/force_daily_games_regen.py            # all games, today
    python3 scripts/force_daily_games_regen.py quiz       # just quiz, today
    python3 scripts/force_daily_games_regen.py all 2026-08-25
"""
import asyncio
import os
import sys
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import DailyQuiz, DailySpellingBee, DailyWordLadder
from app.services.daily_games import generate_quiz, generate_spelling_bee, generate_word_ladder


async def regen_bee(session, puzzle_date: date) -> None:
    existing = (await session.execute(
        select(DailySpellingBee).where(DailySpellingBee.puzzle_date == puzzle_date)
    )).scalar_one_or_none()
    if existing is not None:
        await session.delete(existing)
        await session.flush()
    letters, center, words, source = await generate_spelling_bee(puzzle_date)
    session.add(DailySpellingBee(puzzle_date=puzzle_date, letters=letters, center_letter=center, words=words, source=source))
    print(f"spelling bee: source={source}")


async def regen_ladder(session, puzzle_date: date) -> None:
    existing = (await session.execute(
        select(DailyWordLadder).where(DailyWordLadder.puzzle_date == puzzle_date)
    )).scalar_one_or_none()
    if existing is not None:
        await session.delete(existing)
        await session.flush()
    start, target, allowed, optimal, source = await generate_word_ladder(puzzle_date)
    session.add(DailyWordLadder(puzzle_date=puzzle_date, start_word=start, target_word=target, allowed_words=allowed, optimal_steps=optimal, source=source))
    print(f"word ladder: source={source} ({start} -> {target} in {optimal})")


async def regen_quiz(session, puzzle_date: date) -> None:
    existing = (await session.execute(
        select(DailyQuiz).where(DailyQuiz.puzzle_date == puzzle_date)
    )).scalar_one_or_none()
    if existing is not None:
        await session.delete(existing)
        await session.flush()
    questions, source = await generate_quiz(session, puzzle_date)
    session.add(DailyQuiz(puzzle_date=puzzle_date, questions=questions, source=source))
    print(f"quiz: source={source}")


GAMES = {"bee": regen_bee, "ladder": regen_ladder, "quiz": regen_quiz}


async def main(which: str, puzzle_date: date) -> None:
    targets = GAMES.values() if which == "all" else [GAMES[which]]
    async with AsyncSessionLocal() as session:
        for regen in targets:
            await regen(session, puzzle_date)
        await session.commit()


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which not in GAMES and which != "all":
        print(f"Unknown game {which!r} — expected one of: all, {', '.join(GAMES)}")
        sys.exit(1)
    target_date = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else date.today()
    asyncio.run(main(which, target_date))
