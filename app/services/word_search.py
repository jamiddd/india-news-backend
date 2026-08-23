from __future__ import annotations

import logging
import random
import string
from datetime import date

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DailyWordSearch
from app.services.llm_gen import call_claude_json

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


def _validate_theme_and_words(payload: dict) -> tuple[str, list[str]]:
    theme = str(payload.get("theme") or "").strip()
    words = [str(word).strip().upper() for word in payload.get("words") or []]
    if not theme or len(theme) > 80:
        raise ValueError("Invalid theme")
    if len(words) != 10 or len({w for w in words}) != 10:
        raise ValueError("Need exactly 10 unique words")
    if any(not (4 <= len(w) <= 9) or not w.isalpha() for w in words):
        raise ValueError("Words must be 4-9 letters, alphabetic only")
    return theme, words


async def generate_theme_and_words(puzzle_date: date) -> tuple[str, list[str], str]:
    """Ask Claude for a fresh word-search theme + word list. Any topic is
    fine — this game is meant as a break from the news, not another news
    surface — so the prompt deliberately doesn't steer toward current
    events. Falls back to the curated THEMES bank, deterministically picked
    by date, on failure."""
    system = (
        "You invent word search puzzle themes for a general-audience daily puzzle app. "
        "Pick a fresh, fun theme — any topic (nature, food, sports, history, pop culture, "
        "everyday objects, etc.) — different from an obvious default. Return JSON only: "
        '{"theme": "short theme name", "words": [10 English words, 4-9 letters each, '
        'related to the theme, no duplicates, no proper nouns unless universally known]}'
    )
    data = await call_claude_json(system=system, user_content="Generate today's word search theme and words.", max_tokens=600)
    if data is not None:
        try:
            theme, words = _validate_theme_and_words(data)
            return theme, words, "ai"
        except Exception as exc:
            logger.warning("Word search AI output failed validation: %s", exc)
    seed = int(puzzle_date.strftime("%Y%m%d"))
    theme, source_words = THEMES[seed % len(THEMES)]
    return theme, list(source_words), "curated"


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

    theme, source_words, source = await generate_theme_and_words(puzzle_date)
    try:
        grid, words = place_words(puzzle_date, source_words)
    except RuntimeError as exc:
        # AI-supplied words can occasionally be unplaceable (e.g. an
        # unlucky combination of long words) — the curated bank's words are
        # already known to pack, so fall back to it rather than 500ing.
        logger.warning("Word placement failed for %s words (%s); using curated fallback", source, exc)
        seed = int(puzzle_date.strftime("%Y%m%d"))
        theme, source_words = THEMES[seed % len(THEMES)]
        grid, words = place_words(puzzle_date, source_words)
        source = "curated"
    puzzle = DailyWordSearch(puzzle_date=puzzle_date, theme=theme, grid=grid, words=words, source=source)
    session.add(puzzle)
    await session.commit()
    await session.refresh(puzzle)
    return puzzle
