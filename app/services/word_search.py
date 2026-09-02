from __future__ import annotations

import logging
import random
import re
import string
from datetime import date

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DailyWordSearch
from app.services.apiverve_client import call_apiverve

logger = logging.getLogger(__name__)
SIZE = 12
THEMES = [
    ("Space", ["GALAXY", "PLANET", "COMET", "ORBIT", "NEBULA", "ROCKET", "SATURN", "LUNAR", "METEOR", "COSMOS"]),
    ("India", ["GANGES", "LOTUS", "MONSOON", "HIMALAYA", "SAFFRON", "DELHI", "MUMBAI", "BENGAL", "DECCAN", "DIWALI"]),
    ("Nature", ["FOREST", "RIVER", "OCEAN", "CANYON", "GLACIER", "VOLCANO", "MEADOW", "THUNDER", "BREEZE", "SUNSET"]),
    ("Science", ["ATOM", "CARBON", "ENERGY", "NEURON", "PHOTON", "PLASMA", "MAGNET", "GRAVITY", "OXYGEN", "QUARTZ"]),
    ("Newspaper", ["EDITOR", "COLUMN", "BYLINE", "PRESS", "DAILY", "REPORT", "PUZZLE", "HEADLINE", "JOURNAL", "ARTICLE"]),
    ("Wildlife", ["FALCON", "PANDA", "TIGER", "DOLPHIN", "COBRA", "PEACOCK", "TURTLE", "LEOPARD", "RABBIT", "WHALE"]),
    ("Geography", ["ISLAND", "DESERT", "VALLEY", "PLATEAU", "DELTA", "LAGOON", "TUNDRA", "SAVANNA", "ARCTIC", "EQUATOR"]),
]
DIRECTIONS = ((0, 1), (1, 0), (1, 1), (1, -1), (0, -1), (-1, 0), (-1, -1), (-1, 1))


def theme_and_words_for_date(puzzle_date: date) -> tuple[str, list[str]]:
    """Deterministically pick a theme + word list from the curated THEMES
    bank for a given date — no AI involved, this was already
    dependency-free before the APIVerve swap."""
    seed = int(puzzle_date.strftime("%Y%m%d"))
    theme, source_words = THEMES[seed % len(THEMES)]
    return theme, list(source_words)


async def _apiverve_place_words(words: list[str]) -> tuple[list[str], list[str]] | None:
    """Ask APIVerve's Word Search Generator to place `words` into a grid.
    We already have the word list (curated, not AI) so we don't need the
    Premium `words[]` placement-locations field — just the resulting grid."""
    data = await call_apiverve("wordsearch", {"words": words, "size": SIZE, "difficulty": "medium"}, method="POST")
    if data is None:
        return None
    grid = data.get("grid")
    if not isinstance(grid, list) or len(grid) != SIZE or any(len(row) != SIZE for row in grid):
        logger.warning("APIVerve word search returned an unexpected grid shape")
        return None
    try:
        rows = ["".join(str(cell).upper() for cell in row) for row in grid]
    except Exception as exc:
        logger.warning("APIVerve word search grid could not be flattened: %s", exc)
        return None
    if any(re.search(r"[^A-Z]", row) for row in rows):
        return None
    return rows, sorted(words)


def place_words(puzzle_date: date, words: list[str]) -> tuple[list[str], list[str]]:
    """Pack `words` into the grid, returning (grid rows, sorted placed words).
    Unchanged from the original algorithm — only the source of `words` has
    changed (LLM-generated theme vs. the fixed THEMES rotation)."""
    seed = int(puzzle_date.strftime("%Y%m%d"))
    rng = random.Random(seed)
    words = list(words)
    rng.shuffle(words)
    grid: list[list[str | None]] = [[None for _ in range(SIZE)] for _ in range(SIZE)]

    # Longest-first makes the packing reliable while the shuffled equal-length
    # ordering and random direction/start choices keep each date visually new.
    for word in sorted(words, key=len, reverse=True):
        options = []
        for dr, dc in DIRECTIONS:
            for row in range(SIZE):
                for col in range(SIZE):
                    end_row, end_col = row + (len(word) - 1) * dr, col + (len(word) - 1) * dc
                    if not (0 <= end_row < SIZE and 0 <= end_col < SIZE):
                        continue
                    if all(grid[row + i * dr][col + i * dc] in (None, letter) for i, letter in enumerate(word)):
                        options.append((row, col, dr, dc))
        if not options:
            raise RuntimeError(f"Unable to place word: {word}")
        row, col, dr, dc = rng.choice(options)
        for i, letter in enumerate(word):
            grid[row + i * dr][col + i * dc] = letter

    for row in range(SIZE):
        for col in range(SIZE):
            if grid[row][col] is None:
                grid[row][col] = rng.choice(string.ascii_uppercase)
    return ["".join(row) for row in grid], sorted(words)


async def get_or_create_word_search(session: AsyncSession, puzzle_date: date) -> DailyWordSearch:
    result = await session.execute(select(DailyWordSearch).where(DailyWordSearch.puzzle_date == puzzle_date))
    existing = result.scalar_one_or_none()
    if existing:
        return existing

    lock_key = 75000000 + int(puzzle_date.strftime("%Y%m%d"))
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
    result = await session.execute(select(DailyWordSearch).where(DailyWordSearch.puzzle_date == puzzle_date))
    existing = result.scalar_one_or_none()
    if existing:
        return existing

    theme, source_words = theme_and_words_for_date(puzzle_date)
    placed = await _apiverve_place_words(source_words)
    if placed is not None:
        grid, words = placed
        source = "apiverve"
    else:
        # Local placement is deterministic and already known to pack the
        # curated word lists, so it's a reliable fallback if APIVerve is
        # unavailable or returns something unusable.
        grid, words = place_words(puzzle_date, source_words)
        source = "algorithmic"
    puzzle = DailyWordSearch(puzzle_date=puzzle_date, theme=theme, grid=grid, words=words, source=source)
    session.add(puzzle)
    await session.commit()
    await session.refresh(puzzle)
    return puzzle
