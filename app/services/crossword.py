import json
import logging
import re
from collections import deque
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import DailyCrossword

logger = logging.getLogger(__name__)
INDIA_TZ = ZoneInfo("Asia/Kolkata")
SIZE = 11


def india_today() -> date:
    return datetime.now(INDIA_TZ).date()


def _entries(rows: list[str]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    number = 0
    for row in range(SIZE):
        for col in range(SIZE):
            if rows[row][col] == "#":
                continue
            across = (col == 0 or rows[row][col - 1] == "#") and col + 2 < SIZE and rows[row][col + 1] != "#" and rows[row][col + 2] != "#"
            down = (row == 0 or rows[row - 1][col] == "#") and row + 2 < SIZE and rows[row + 1][col] != "#" and rows[row + 2][col] != "#"
            if not across and not down:
                continue
            number += 1
            if across:
                length = 0
                while col + length < SIZE and rows[row][col + length] != "#":
                    length += 1
                entries.append({"number": number, "direction": "across", "row": row, "col": col, "length": length})
            if down:
                length = 0
                while row + length < SIZE and rows[row + length][col] != "#":
                    length += 1
                entries.append({"number": number, "direction": "down", "row": row, "col": col, "length": length})
    return entries


def _entry_answer(rows: list[str], entry: dict[str, Any]) -> str:
    dr, dc = (0, 1) if entry["direction"] == "across" else (1, 0)
    return "".join(rows[entry["row"] + i * dr][entry["col"] + i * dc] for i in range(entry["length"]))


def validate_and_normalize(payload: dict[str, Any]) -> dict[str, Any]:
    rows = [str(row).upper() for row in payload.get("rows", [])]
    if len(rows) != SIZE or any(len(row) != SIZE for row in rows):
        raise ValueError("Crossword must be exactly 11x11")
    if any(re.search(r"[^A-Z#]", row) for row in rows):
        raise ValueError("Grid may contain only A-Z and #")
    for row in range(SIZE):
        for col in range(SIZE):
            if (rows[row][col] == "#") != (rows[SIZE - 1 - row][SIZE - 1 - col] == "#"):
                raise ValueError("Block pattern must have 180-degree symmetry")

    white = {(r, c) for r in range(SIZE) for c in range(SIZE) if rows[r][c] != "#"}
    if not white:
        raise ValueError("Grid is empty")
    seen = {next(iter(white))}
    queue = deque(seen)
    while queue:
        row, col = queue.popleft()
        for nr, nc in ((row - 1, col), (row + 1, col), (row, col - 1), (row, col + 1)):
            if (nr, nc) in white and (nr, nc) not in seen:
                seen.add((nr, nc))
                queue.append((nr, nc))
    if seen != white:
        raise ValueError("All open cells must be connected")

    horizontal_runs = [run for row in rows for run in row.split("#") if run]
    vertical_runs = [
        run
        for col in range(SIZE)
        for run in "".join(rows[row][col] for row in range(SIZE)).split("#")
        if run
    ]
    if any(len(run) == 2 for run in horizontal_runs + vertical_runs):
        raise ValueError("Two-letter entries are not allowed")

    entries = _entries(rows)
    covered: set[tuple[int, int]] = set()
    for entry in entries:
        if entry["length"] < 3:
            raise ValueError("Entries shorter than three letters are not allowed")
        dr, dc = (0, 1) if entry["direction"] == "across" else (1, 0)
        covered.update((entry["row"] + i * dr, entry["col"] + i * dc) for i in range(entry["length"]))
    if covered != white:
        raise ValueError("Every open cell must belong to an entry")

    supplied = {
        (int(item["number"]), str(item["direction"]).lower()): str(item["clue"]).strip()
        for item in payload.get("clues", [])
    }
    clues = []
    for entry in entries:
        key = (entry["number"], entry["direction"])
        clue = supplied.get(key)
        if not clue:
            raise ValueError(f"Missing clue for {key}")
        clues.append({**entry, "clue": clue})

    return {
        "solution": rows,
        "grid": ["".join("#" if char == "#" else "." for char in row) for row in rows],
        "clues": clues,
    }


def fallback_puzzle() -> dict[str, Any]:
    rows = ["###########"] * 3 + [
        "###HEART###",
        "###EMBER###",
        "###ABUSE###",
        "###RESIN###",
        "###TREND###",
    ] + ["###########"] * 3
    across_clues = [
        "Organ that pumps blood", "Glowing coal in a fading fire", "Cruel or improper treatment",
        "Sticky plant substance", "A general direction of change",
    ]
    down_clues = [
        "Symbolic center of emotion", "Small glowing piece of wood", "To mistreat someone",
        "Substance used in varnish", "Popular tendency or fashion",
    ]
    clues = []
    ai = di = 0
    for entry in _entries(rows):
        if entry["direction"] == "across":
            clue = across_clues[ai]
            ai += 1
        else:
            clue = down_clues[di]
            di += 1
        clues.append({"number": entry["number"], "direction": entry["direction"], "clue": clue})
    return validate_and_normalize({"rows": rows, "clues": clues})


def _extract_json(raw: str) -> dict[str, Any]:
    cleaned = raw.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.DOTALL)
    if fenced:
        cleaned = fenced.group(1)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


async def generate_puzzle() -> tuple[dict[str, Any], str]:
    if settings.ANTHROPIC_API_KEY:
        prompt = """Create a medium general-knowledge American-style crossword. Return JSON only:
{"rows":[11 strings of exactly 11 A-Z/# characters],"clues":[{"number":1,"direction":"across","clue":"..."}]}
The # pattern must have 180-degree rotational symmetry. Every open cell must be connected and part of an Across or Down answer of at least 3 letters. Number cells in standard row-major crossword order and include exactly one clue for every Across and Down entry. Use clear, factual, family-friendly clues."""
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=45) as client:
                    response = await client.post(
                        "https://api.anthropic.com/v1/messages",
                        headers={
                            "x-api-key": settings.ANTHROPIC_API_KEY,
                            "anthropic-version": "2023-06-01",
                            "content-type": "application/json",
                        },
                        json={
                            "model": "claude-haiku-4-5",
                            "max_tokens": 5000,
                            "temperature": 0.8,
                            "messages": [{"role": "user", "content": prompt}],
                        },
                    )
                    response.raise_for_status()
                    raw = response.json()["content"][0]["text"]
                    return validate_and_normalize(_extract_json(raw)), "ai"
            except Exception as exc:
                logger.warning("Crossword generation attempt %s failed: %s", attempt + 1, exc)
    return fallback_puzzle(), "fallback"


async def get_or_create_puzzle(session: AsyncSession, puzzle_date: date) -> DailyCrossword:
    result = await session.execute(select(DailyCrossword).where(DailyCrossword.puzzle_date == puzzle_date))
    existing = result.scalar_one_or_none()
    if existing:
        return existing

    # Serializes first-request generation across all four Uvicorn workers.
    lock_key = 73000000 + int(puzzle_date.strftime("%Y%m%d"))
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
    result = await session.execute(select(DailyCrossword).where(DailyCrossword.puzzle_date == puzzle_date))
    existing = result.scalar_one_or_none()
    if existing:
        return existing

    generated, source = await generate_puzzle()
    puzzle = DailyCrossword(
        puzzle_date=puzzle_date,
        size=SIZE,
        grid=generated["grid"],
        clues=generated["clues"],
        solution=generated["solution"],
        source=source,
    )
    session.add(puzzle)
    await session.commit()
    await session.refresh(puzzle)
    return puzzle
