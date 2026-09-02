from __future__ import annotations

import logging
import random
from datetime import date

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DailySudoku
from app.services.apiverve_client import call_apiverve

logger = logging.getLogger(__name__)


def _count_solutions(board: list[int], limit: int = 2) -> int:
    best_index = -1
    best_candidates: list[int] = []
    for index, value in enumerate(board):
        if value:
            continue
        row, col = divmod(index, 9)
        used = {board[row * 9 + i] for i in range(9)} | {board[i * 9 + col] for i in range(9)}
        used |= {
            board[r * 9 + c]
            for r in range((row // 3) * 3, (row // 3) * 3 + 3)
            for c in range((col // 3) * 3, (col // 3) * 3 + 3)
        }
        candidates = [number for number in range(1, 10) if number not in used]
        if not candidates:
            return 0
        if best_index == -1 or len(candidates) < len(best_candidates):
            best_index, best_candidates = index, candidates
    if best_index == -1:
        return 1
    solutions = 0
    for candidate in best_candidates:
        board[best_index] = candidate
        solutions += _count_solutions(board, limit - solutions)
        if solutions >= limit:
            break
    board[best_index] = 0
    return solutions


def _solve(board: list[int]) -> list[int] | None:
    """Backtracking solver — fills in `board` (0 = blank) and returns the
    solved grid, or None if it's unsolvable. Assumes the board has at most
    one solution (true for any well-formed sudoku); if it has several this
    returns whichever the search finds first, which is fine here since it's
    only used to derive the solution for a puzzle APIVerve already gave us,
    not to construct one from scratch."""
    board = board.copy()

    def solve() -> bool:
        best_index = -1
        best_candidates: list[int] = []
        for index, value in enumerate(board):
            if value:
                continue
            row, col = divmod(index, 9)
            used = {board[row * 9 + i] for i in range(9)} | {board[i * 9 + col] for i in range(9)}
            used |= {
                board[r * 9 + c]
                for r in range((row // 3) * 3, (row // 3) * 3 + 3)
                for c in range((col // 3) * 3, (col // 3) * 3 + 3)
            }
            candidates = [number for number in range(1, 10) if number not in used]
            if not candidates:
                return False
            if best_index == -1 or len(candidates) < len(best_candidates):
                best_index, best_candidates = index, candidates
        if best_index == -1:
            return True
        for candidate in best_candidates:
            board[best_index] = candidate
            if solve():
                return True
            board[best_index] = 0
        return False

    return board if solve() else None


def generate_daily_sudoku(puzzle_date: date) -> tuple[list[int], list[int]]:
    rng = random.Random(int(puzzle_date.strftime("%Y%m%d")))

    def shuffled_groups() -> list[int]:
        groups = [0, 1, 2]
        rng.shuffle(groups)
        result = []
        for group in groups:
            within = [0, 1, 2]
            rng.shuffle(within)
            result.extend(group * 3 + item for item in within)
        return result

    rows, cols = shuffled_groups(), shuffled_groups()
    numbers = list(range(1, 10))
    rng.shuffle(numbers)
    solution = [numbers[(row * 3 + row // 3 + col) % 9] for row in rows for col in cols]
    puzzle = solution.copy()
    removal_order = list(range(81))
    rng.shuffle(removal_order)
    for index in removal_order:
        if sum(value != 0 for value in puzzle) <= 36:
            break
        previous = puzzle[index]
        puzzle[index] = 0
        if _count_solutions(puzzle.copy()) != 1:
            puzzle[index] = previous
    return puzzle, solution


def _cells_from_chars(chars: str) -> list[int] | None:
    cells: list[int] = []
    for char in chars:
        if char.isdigit():
            cells.append(int(char))
        elif char in ("-", ".", "_", "x", "X", " "):
            cells.append(0)
        else:
            return None
    return cells


def _parse_grid(value) -> list[int] | None:
    """APIVerve's exact grid shape isn't nailed down by the public docs, so
    accept anything reasonable: an 81-char string (any non-digit treated as
    blank), a string with row separators (newlines/spaces) that leaves 81
    digits once stripped, or a 9x9 nested list of ints/single-char strings."""
    if isinstance(value, str):
        stripped = "".join(ch for ch in value if not ch.isspace())
        if len(stripped) == 81:
            return _cells_from_chars(stripped)
        return None
    if isinstance(value, list) and len(value) == 9 and all(isinstance(row, (list, str)) for row in value):
        flat = "".join("".join(str(cell) for cell in row) if isinstance(row, list) else row for row in value)
        if len(flat) == 81:
            return _cells_from_chars(flat)
    return None


def _extract_grid(section) -> list[int] | None:
    """A `puzzle`/`solution` section might itself be the grid (string or
    9x9 list), or a dict wrapping it under a `grid`/`board` key."""
    if isinstance(section, dict):
        for key in ("grid", "board", "puzzle", "solution"):
            if key in section:
                parsed = _parse_grid(section[key])
                if parsed is not None:
                    return parsed
        return None
    return _parse_grid(section)


async def _apiverve_sudoku() -> tuple[list[int], list[int]] | None:
    """The free tier gives the puzzle grid but the `solution` field is
    Premium-gated (comes back None) — we don't actually need APIVerve for
    that part, since a well-formed sudoku has exactly one solution and
    _solve already exists (originally for the algorithmic generator's own
    uniqueness check), so solve the puzzle locally instead."""
    data = await call_apiverve("sudoku", {"difficulty": "medium"})
    if data is None:
        return None
    puzzle = _extract_grid(data.get("puzzle"))
    if puzzle is None:
        logger.warning("APIVerve sudoku response had an unrecognized puzzle grid shape: %r", data)
        return None
    solution = _solve(puzzle)
    if solution is None:
        logger.warning("APIVerve sudoku puzzle grid has no solution: %r", puzzle)
        return None
    return puzzle, solution


async def get_or_create_sudoku(session: AsyncSession, puzzle_date: date) -> DailySudoku:
    result = await session.execute(select(DailySudoku).where(DailySudoku.puzzle_date == puzzle_date))
    existing = result.scalar_one_or_none()
    if existing:
        return existing

    lock_key = 74000000 + int(puzzle_date.strftime("%Y%m%d"))
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
    result = await session.execute(select(DailySudoku).where(DailySudoku.puzzle_date == puzzle_date))
    existing = result.scalar_one_or_none()
    if existing:
        return existing

    fetched = await _apiverve_sudoku()
    puzzle, solution = fetched if fetched is not None else generate_daily_sudoku(puzzle_date)
    row = DailySudoku(puzzle_date=puzzle_date, puzzle=puzzle, solution=solution)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row
