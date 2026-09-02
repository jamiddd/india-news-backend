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


def _grid_from_string(text_grid: str) -> list[int] | None:
    if not isinstance(text_grid, str) or len(text_grid) != 81:
        return None
    cells: list[int] = []
    for char in text_grid:
        if char.isdigit():
            cells.append(int(char))
        elif char in ("-", "."):
            cells.append(0)
        else:
            return None
    return cells


async def _apiverve_sudoku() -> tuple[list[int], list[int]] | None:
    data = await call_apiverve("sudoku", {"difficulty": "medium"})
    if data is None:
        return None
    puzzle = _grid_from_string(((data.get("puzzle") or {}).get("grid")))
    solution = _grid_from_string(((data.get("solution") or {}).get("grid")))
    if puzzle is None or solution is None or 0 in solution:
        logger.warning("APIVerve sudoku response had an invalid grid")
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
