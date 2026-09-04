from __future__ import annotations

import logging
import random
import re
from collections import deque
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DailyCrossword
from app.services.apiverve_client import call_apiverve

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


def validate_and_normalize(payload: dict[str, Any], require_symmetry: bool = True) -> dict[str, Any]:
    rows = [str(row).upper() for row in payload.get("rows", [])]
    if len(rows) != SIZE or any(len(row) != SIZE for row in rows):
        raise ValueError("Crossword must be exactly 11x11")
    if any(re.search(r"[^A-Z#]", row) for row in rows):
        raise ValueError("Grid may contain only A-Z and #")
    if require_symmetry:
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


def legacy_fallback_puzzle() -> dict[str, Any]:
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


# Curated, dependency-free fallback corpus. The placement engine below uses a
# date seed, so it produces a stable shared puzzle for a given day but rotates
# both the answers and the board shape across days.
WORD_BANK = [
    ("AMAZON", "River with the world's largest drainage basin"),
    ("ANCHOR", "Heavy object that keeps a vessel in place"),
    ("APOLLO", "Greek god associated with music and the sun"),
    ("ARCTIC", "Polar region surrounding the North Pole"),
    ("ATOM", "Smallest unit of an element"),
    ("AURORA", "Natural lights seen in polar skies"),
    ("BAMBOO", "Fast-growing plant eaten by pandas"),
    ("BINARY", "Number system using only zero and one"),
    ("CANAL", "Artificial waterway for boats"),
    ("CANYON", "Deep valley with steep sides"),
    ("CARBON", "Element found in diamonds and graphite"),
    ("CASPIAN", "World's largest inland body of water"),
    ("COBALT", "Blue-associated metallic element"),
    ("COMET", "Icy body that can develop a glowing tail"),
    ("CORAL", "Marine organism that forms tropical reefs"),
    ("DELTA", "River landform shaped by deposited sediment"),
    ("ECLIPSE", "Event in which one celestial body obscures another"),
    ("EVEREST", "Earth's highest mountain above sea level"),
    ("FALCON", "Fast-flying bird of prey"),
    ("FOSSIL", "Preserved remains or trace of ancient life"),
    ("GALAXY", "Huge gravitationally bound system of stars"),
    ("GANGES", "Major river flowing across northern India"),
    ("GEYSER", "Hot spring that erupts water and steam"),
    ("GLACIER", "Slow-moving mass of land ice"),
    ("HABITAT", "Natural home of a plant or animal"),
    ("HELIUM", "Gas commonly used to fill balloons"),
    ("INDUS", "River associated with an ancient South Asian civilization"),
    ("ISLAND", "Land completely surrounded by water"),
    ("JASMINE", "Fragrant flower used in tea and perfume"),
    ("JUPITER", "Largest planet in the Solar System"),
    ("KARMA", "Concept linking actions with consequences"),
    ("LAGOON", "Shallow water separated from a larger sea"),
    ("LOTUS", "Aquatic flower that is India's national flower"),
    ("MAGNET", "Object that attracts iron"),
    ("MANTLE", "Layer of Earth between crust and core"),
    ("MONSOON", "Seasonal wind system bringing heavy rain"),
    ("NEBULA", "Cloud of gas and dust in space"),
    ("NEURON", "Cell that transmits nerve impulses"),
    ("NICKEL", "Metal that gives its name to a US coin"),
    ("OASIS", "Fertile spot in a desert"),
    ("OCEAN", "Vast body of salt water"),
    ("ORBIT", "Curved path around a planet or star"),
    ("ORCHID", "Flowering plant known for complex blooms"),
    ("OXYGEN", "Gas required for human respiration"),
    ("PANDA", "Black-and-white bear native to China"),
    ("PARSEC", "Astronomical unit of distance"),
    ("PHOTON", "Quantum particle of light"),
    ("PLASMA", "State of matter found in stars"),
    ("QUARTZ", "Common mineral made of silicon and oxygen"),
    ("RADAR", "System using radio waves to detect objects"),
    ("RADIUS", "Line from a circle's center to its edge"),
    ("SAFFRON", "Spice obtained from crocus stigmas"),
    ("SATURN", "Planet famous for its prominent rings"),
    ("SAVANNA", "Tropical grassland with scattered trees"),
    ("SONAR", "Underwater detection system using sound"),
    ("TUNDRA", "Cold, treeless biome"),
    ("TURBINE", "Machine whose rotor is driven by fluid"),
    ("VECTOR", "Quantity having magnitude and direction"),
    ("VOLCANO", "Opening in Earth's crust that can erupt lava"),
    ("ZENITH", "Point in the sky directly overhead"),
]


def _can_place(grid: list[list[str | None]], answer: str, row: int, col: int, direction: str, require_crossing: bool) -> bool:
    dr, dc = (0, 1) if direction == "across" else (1, 0)
    end_row, end_col = row + (len(answer) - 1) * dr, col + (len(answer) - 1) * dc
    if row < 0 or col < 0 or end_row >= SIZE or end_col >= SIZE:
        return False
    before, after = (row - dr, col - dc), (end_row + dr, end_col + dc)
    for rr, cc in (before, after):
        if 0 <= rr < SIZE and 0 <= cc < SIZE and grid[rr][cc] is not None:
            return False
    crossings = 0
    for index, letter in enumerate(answer):
        rr, cc = row + index * dr, col + index * dc
        existing = grid[rr][cc]
        if existing is not None:
            if existing != letter:
                return False
            crossings += 1
        else:
            neighbors = ((rr - 1, cc), (rr + 1, cc)) if direction == "across" else ((rr, cc - 1), (rr, cc + 1))
            if any(0 <= nr < SIZE and 0 <= nc < SIZE and grid[nr][nc] is not None for nr, nc in neighbors):
                return False
    return crossings > 0 if require_crossing else True


def _pack_word_bank(word_bank: list[tuple[str, str]], base_seed: int) -> tuple[list[list[str | None]], list[dict[str, Any]]]:
    """Greedily place (answer, clue) pairs into an SIZExSIZE grid, crossing
    words where possible. Order-sensitive, so try several deterministic
    shuffles and keep the densest result. Returns (grid, placed) — placed may
    have fewer than 8 entries if word_bank doesn't pack well; caller decides
    what "too few" means for its use case.
    """
    best_grid: list[list[str | None]] = []
    best_placed: list[dict[str, Any]] = []
    long_enough = [item for item in word_bank if 6 <= len(item[0]) <= 9]
    if not long_enough:
        return [[None for _ in range(SIZE)] for _ in range(SIZE)], []

    for packing_attempt in range(24):
        rng = random.Random(base_seed + packing_attempt * 7919)
        candidates = list(word_bank)
        rng.shuffle(candidates)
        grid: list[list[str | None]] = [[None for _ in range(SIZE)] for _ in range(SIZE)]
        placed: list[dict[str, Any]] = []

        first_answer, first_clue = next(item for item in candidates if 6 <= len(item[0]) <= 9)
        first_row = SIZE // 2
        first_col = (SIZE - len(first_answer)) // 2
        for i, letter in enumerate(first_answer):
            grid[first_row][first_col + i] = letter
        placed.append({"answer": first_answer, "clue": first_clue, "row": first_row, "col": first_col, "direction": "across"})

        for answer, clue in candidates:
            if answer == first_answer or len(placed) >= 16:
                continue
            options: list[tuple[int, int, str]] = []
            for letter_index, letter in enumerate(answer):
                for row in range(SIZE):
                    for col in range(SIZE):
                        if grid[row][col] != letter:
                            continue
                        for direction in ("across", "down"):
                            start_row = row - (letter_index if direction == "down" else 0)
                            start_col = col - (letter_index if direction == "across" else 0)
                            if _can_place(grid, answer, start_row, start_col, direction, require_crossing=True):
                                options.append((start_row, start_col, direction))
            if not options:
                continue
            row, col, direction = rng.choice(options)
            dr, dc = (0, 1) if direction == "across" else (1, 0)
            for i, letter in enumerate(answer):
                grid[row + i * dr][col + i * dc] = letter
            placed.append({"answer": answer, "clue": clue, "row": row, "col": col, "direction": direction})

        if len(placed) > len(best_placed):
            best_grid, best_placed = grid, placed
        if len(best_placed) >= 14:
            break

    return best_grid, best_placed


def _grid_to_puzzle(grid: list[list[str | None]], placed: list[dict[str, Any]]) -> dict[str, Any]:
    rows = ["".join(cell or "#" for cell in row) for row in grid]
    computed = _entries(rows)
    clue_by_slot = {(item["row"], item["col"], item["direction"]): item["clue"] for item in placed}
    clues = [
        {
            "number": entry["number"],
            "direction": entry["direction"],
            "clue": clue_by_slot[(entry["row"], entry["col"], entry["direction"])],
        }
        for entry in computed
    ]
    return validate_and_normalize({"rows": rows, "clues": clues}, require_symmetry=False)


def algorithmic_fallback_puzzle(puzzle_date: date) -> dict[str, Any]:
    base_seed = int(puzzle_date.strftime("%Y%m%d"))
    grid, placed = _pack_word_bank(WORD_BANK, base_seed)
    if len(placed) < 8:
        logger.error("Algorithmic crossword placed only %s entries; using legacy fallback", len(placed))
        return legacy_fallback_puzzle()
    return _grid_to_puzzle(grid, placed)


_WORD_RE = re.compile(r"^[A-Z]{3,9}$")


async def _apiverve_word_bank() -> list[tuple[str, str]] | None:
    """Ask APIVerve's Crossword Generator for its own across/down word+clue
    list — we ignore its grid layout, since _pack_word_bank already does
    that reliably in plain Python (11x11 symmetric packing is a harder
    structured-generation task than sourcing varied vocabulary+clues)."""
    data = await call_apiverve("crossword", {"size": "large", "difficulty": "medium", "theme": "random"})
    if data is None:
        return None
    entries = list(data.get("across") or []) + list(data.get("down") or [])
    seen: set[str] = set()
    bank: list[tuple[str, str]] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        answer = str(item.get("answer", "")).upper().strip()
        clue = str(item.get("clue", "")).strip()
        if not _WORD_RE.match(answer) or not clue or answer in seen:
            continue
        seen.add(answer)
        bank.append((answer, clue))
    return bank if len(bank) >= 12 else None


async def generate_puzzle(puzzle_date: date) -> tuple[dict[str, Any], str]:
    base_seed = int(puzzle_date.strftime("%Y%m%d"))
    word_bank = await _apiverve_word_bank()
    if word_bank is not None:
        grid, placed = _pack_word_bank(word_bank, base_seed)
        if len(placed) >= 8:
            try:
                return _grid_to_puzzle(grid, placed), "apiverve"
            except Exception as exc:
                logger.warning("Crossword apiverve word bank packed but failed validation: %s", exc)
        else:
            logger.warning("Crossword apiverve word bank only packed %s entries", len(placed))
    return algorithmic_fallback_puzzle(puzzle_date), "algorithmic"


async def get_or_create_puzzle(
    session: AsyncSession, puzzle_date: date, *, allow_upgrade: bool = False
) -> DailyCrossword:
    """Return the day's crossword, generating it if this is the first ask.

    `allow_upgrade` controls one specific behaviour: re-attempting APIVerve for
    a day that already has an *algorithmic* puzzle stored, to replace it with
    better clues. Only the nightly scheduler passes it.

    It has to be off for user requests. A stored algorithmic puzzle never
    becomes non-algorithmic on its own, so an unconditional upgrade retry fires
    a fresh APIVerve call on *every* request for that day, forever — and each
    one that succeeds bills a credit. On the ~60-credit monthly budget this
    backend runs on, one failed generation could drain the month in an hour of
    ordinary traffic. Bounded here to a single attempt per day, from the
    scheduler, where a failure costs one credit and waits until tomorrow."""
    result = await session.execute(select(DailyCrossword).where(DailyCrossword.puzzle_date == puzzle_date))
    existing = result.scalar_one_or_none()
    if existing and (not allow_upgrade or existing.source != "algorithmic"):
        return existing

    # Serializes first-request generation across all four Uvicorn workers.
    lock_key = 73000000 + int(puzzle_date.strftime("%Y%m%d"))
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
    result = await session.execute(select(DailyCrossword).where(DailyCrossword.puzzle_date == puzzle_date))
    existing = result.scalar_one_or_none()
    if existing and (not allow_upgrade or existing.source != "algorithmic"):
        return existing

    generated, source = await generate_puzzle(puzzle_date)
    if existing is not None:
        existing.grid = generated["grid"]
        existing.clues = generated["clues"]
        existing.solution = generated["solution"]
        existing.source = source
        await session.commit()
        await session.refresh(existing)
        return existing
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
