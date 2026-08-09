from datetime import date

from app.services.sudoku import _count_solutions, generate_daily_sudoku


def test_daily_sudoku_is_deterministic_valid_and_unique():
    puzzle, solution = generate_daily_sudoku(date(2026, 8, 9))
    assert (puzzle, solution) == generate_daily_sudoku(date(2026, 8, 9))
    assert len(puzzle) == len(solution) == 81
    assert sum(value != 0 for value in puzzle) == 36
    assert all(not value or value == solution[index] for index, value in enumerate(puzzle))
    assert _count_solutions(puzzle.copy()) == 1
    for index in range(9):
        assert {solution[index * 9 + col] for col in range(9)} == set(range(1, 10))
        assert {solution[row * 9 + index] for row in range(9)} == set(range(1, 10))


def test_different_dates_get_different_sudokus():
    assert generate_daily_sudoku(date(2026, 8, 9)) != generate_daily_sudoku(date(2026, 8, 10))
