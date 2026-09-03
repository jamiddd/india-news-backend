"""
One-off: replace TODAY's (or a given date's) cached Crossword, Sudoku, Word
Search, Spelling Bee, Word Ladder, and Quiz with freshly generated content
now that each of those generators tries APIVerve first (see
services/apiverve_client.py + the individual game services), instead of
waiting for the date to roll over. Also replaces just the `quote` half of
the Editorial (Word of the Day / Quote of the Day) row — Word of the Day,
its background image, and historical_events are left untouched, since only
Quote of the Day was moved to APIVerve.

Always overwrites the existing row for the target date, regardless of its
current `source` — this is a "replace today's content right now" script,
not the conditional "only if still on fallback" check that
force_daily_games_regen.py does. Requires APIVERVE_API_KEY to be set for
these to actually source from APIVerve; without it, each generator falls
straight through to its existing Claude/curated/algorithmic path same as
before, so this script still works, it just won't change anything
meaningfully.

Usage (inside the app container):
    python3 scripts/force_apiverve_regen.py [YYYY-MM-DD]
"""
import asyncio
import os
import sys
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import (
    DailyCrossword,
    DailyEditorial,
    DailyQuiz,
    DailySpellingBee,
    DailySudoku,
    DailyWordLadder,
    DailyWordSearch,
)
from app.services.crossword import generate_puzzle
from app.services.daily_games import generate_quiz, generate_spelling_bee, generate_word_ladder
from app.services.editorial_features import _apiverve_quote, _recent_words_and_authors
from app.services.sudoku import _apiverve_sudoku, generate_daily_sudoku
from app.services.word_search import _apiverve_place_words, place_words, theme_and_words_for_date


async def _replace(session, model, puzzle_date: date, build_kwargs: dict) -> None:
    existing = (await session.execute(
        select(model).where(model.puzzle_date == puzzle_date)
    )).scalar_one_or_none()
    if existing is not None:
        await session.delete(existing)
        await session.flush()
    session.add(model(puzzle_date=puzzle_date, **build_kwargs))


async def regen_crossword(session, puzzle_date: date) -> None:
    generated, source = await generate_puzzle(puzzle_date)
    await _replace(session, DailyCrossword, puzzle_date, {
        "size": len(generated["grid"]),
        "grid": generated["grid"],
        "clues": generated["clues"],
        "solution": generated["solution"],
        "source": source,
    })
    print(f"crossword: regenerated, source={source}")


async def regen_sudoku(session, puzzle_date: date) -> None:
    fetched = await _apiverve_sudoku()
    puzzle, solution = fetched if fetched is not None else generate_daily_sudoku(puzzle_date)
    await _replace(session, DailySudoku, puzzle_date, {"puzzle": puzzle, "solution": solution})
    print(f"sudoku: regenerated, source={'apiverve' if fetched is not None else 'algorithmic'}")


async def regen_word_search(session, puzzle_date: date) -> None:
    theme, source_words = theme_and_words_for_date(puzzle_date)
    placed = await _apiverve_place_words(source_words)
    if placed is not None:
        grid, words = placed
        source = "apiverve"
    else:
        grid, words = place_words(puzzle_date, source_words)
        source = "algorithmic"
    await _replace(session, DailyWordSearch, puzzle_date, {"theme": theme, "grid": grid, "words": words, "source": source})
    print(f"word search: regenerated, source={source}, theme={theme}")


async def regen_bee(session, puzzle_date: date) -> None:
    letters, center, words, source = await generate_spelling_bee(puzzle_date)
    await _replace(session, DailySpellingBee, puzzle_date, {"letters": letters, "center_letter": center, "words": words, "source": source})
    print(f"spelling bee: regenerated, source={source}")


async def regen_ladder(session, puzzle_date: date) -> None:
    start, target, allowed, optimal, source = await generate_word_ladder(puzzle_date)
    await _replace(session, DailyWordLadder, puzzle_date, {"start_word": start, "target_word": target, "allowed_words": allowed, "optimal_steps": optimal, "source": source})
    print(f"word ladder: regenerated, source={source} ({start} -> {target} in {optimal})")


async def regen_quiz(session, puzzle_date: date) -> None:
    questions, source = await generate_quiz(puzzle_date)
    await _replace(session, DailyQuiz, puzzle_date, {"questions": questions, "source": source})
    print(f"quiz: regenerated, source={source}")


async def regen_quote(session, puzzle_date: date) -> None:
    existing = (await session.execute(
        select(DailyEditorial).where(DailyEditorial.feature_date == puzzle_date)
    )).scalar_one_or_none()
    if existing is None:
        print("quote of the day: no existing editorial row for this date — run the app's normal endpoint first to create one")
        return
    _, recent_authors, _ = await _recent_words_and_authors(session, puzzle_date)
    quote = await _apiverve_quote(recent_authors)
    if quote is None:
        print("quote of the day: APIVerve unavailable, left existing quote untouched")
        return
    existing.quote = quote
    print(f"quote of the day: replaced, source=apiverve — {quote['quote']!r} — {quote['author']}")


async def main(target_date: date) -> None:
    async with AsyncSessionLocal() as session:
        await regen_crossword(session, target_date)
        await regen_sudoku(session, target_date)
        await regen_word_search(session, target_date)
        await regen_bee(session, target_date)
        await regen_ladder(session, target_date)
        await regen_quiz(session, target_date)
        await regen_quote(session, target_date)
        await session.commit()


if __name__ == "__main__":
    target_date = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()
    asyncio.run(main(target_date))
