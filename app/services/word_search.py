from __future__ import annotations

import random
import string
from datetime import date

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DailyWordSearch

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


def generate_daily_word_search(puzzle_date: date) -> tuple[str, list[str], list[str]]:
    seed = int(puzzle_date.strftime("%Y%m%d"))
    rng = random.Random(seed)
    theme, source_words = THEMES[seed % len(THEMES)]
    words = list(source_words)
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
    return theme, ["".join(row) for row in grid], sorted(words)


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

    theme, grid, words = generate_daily_word_search(puzzle_date)
    puzzle = DailyWordSearch(puzzle_date=puzzle_date, theme=theme, grid=grid, words=words)
    session.add(puzzle)
    await session.commit()
    await session.refresh(puzzle)
    return puzzle
