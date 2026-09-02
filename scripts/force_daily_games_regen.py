"""
One-off: regenerate today's (or a given date's) Spelling Bee, Word Ladder,
and/or Quiz right now if — and only if — they're currently on the
curated fallback, instead of waiting for the next scheduler run. Useful
after an APIVerve outage/free-tier miss to retry without needlessly
re-rolling a game that's already sourced from APIVerve.

Checks each game's existing row's `source` column before doing anything:
skips (no API call, no DB write) if it's already "apiverve", regenerates if
it's "curated" or the row doesn't exist yet. Pass --force to regenerate
regardless of current source.

get_or_create_daily_games (the real request path) does NOT do this check —
it skips generation entirely if a row already exists for the date,
regardless of source, so a curated row from before a fix stays stuck
curated until the date rolls over. This script exists to unstick that on
demand. Mutates the DB when it does regenerate — not read-only like the
other scripts/ here.

Usage (inside the app or crossword_scheduler container):
    python3 scripts/force_daily_games_regen.py [bee|ladder|quiz|all] [YYYY-MM-DD] [--force]
    python3 scripts/force_daily_games_regen.py                # all games, today, only if curated
    python3 scripts/force_daily_games_regen.py quiz           # just quiz, today, only if curated
    python3 scripts/force_daily_games_regen.py all 2026-08-25 --force
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


async def _needs_regen(session, model, puzzle_date: date, force: bool):
    existing = (await session.execute(
        select(model).where(model.puzzle_date == puzzle_date)
    )).scalar_one_or_none()
    if existing is None:
        return None, True
    if not force and existing.source == "apiverve":
        return existing, False
    return existing, True


async def regen_bee(session, puzzle_date: date, force: bool) -> None:
    existing, should = await _needs_regen(session, DailySpellingBee, puzzle_date, force)
    if not should:
        print(f"spelling bee: already source={existing.source}, skipping")
        return
    if existing is not None:
        await session.delete(existing)
        await session.flush()
    letters, center, words, source = await generate_spelling_bee(puzzle_date)
    session.add(DailySpellingBee(puzzle_date=puzzle_date, letters=letters, center_letter=center, words=words, source=source))
    print(f"spelling bee: regenerated, source={source}")


async def regen_ladder(session, puzzle_date: date, force: bool) -> None:
    existing, should = await _needs_regen(session, DailyWordLadder, puzzle_date, force)
    if not should:
        print(f"word ladder: already source={existing.source}, skipping")
        return
    if existing is not None:
        await session.delete(existing)
        await session.flush()
    start, target, allowed, optimal, source = await generate_word_ladder(puzzle_date)
    session.add(DailyWordLadder(puzzle_date=puzzle_date, start_word=start, target_word=target, allowed_words=allowed, optimal_steps=optimal, source=source))
    print(f"word ladder: regenerated, source={source} ({start} -> {target} in {optimal})")


async def regen_quiz(session, puzzle_date: date, force: bool) -> None:
    existing, should = await _needs_regen(session, DailyQuiz, puzzle_date, force)
    if not should:
        print(f"quiz: already source={existing.source}, skipping")
        return
    if existing is not None:
        await session.delete(existing)
        await session.flush()
    questions, source = await generate_quiz(puzzle_date)
    session.add(DailyQuiz(puzzle_date=puzzle_date, questions=questions, source=source))
    print(f"quiz: regenerated, source={source}")


GAMES = {"bee": regen_bee, "ladder": regen_ladder, "quiz": regen_quiz}


async def main(which: str, puzzle_date: date, force: bool) -> None:
    targets = GAMES.values() if which == "all" else [GAMES[which]]
    async with AsyncSessionLocal() as session:
        for regen in targets:
            await regen(session, puzzle_date, force)
        await session.commit()


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--force"]
    force = "--force" in sys.argv
    which = args[0] if len(args) > 0 else "all"
    if which not in GAMES and which != "all":
        print(f"Unknown game {which!r} — expected one of: all, {', '.join(GAMES)}")
        sys.exit(1)
    target_date = date.fromisoformat(args[1]) if len(args) > 1 else date.today()
    asyncio.run(main(which, target_date, force))
