import pytest

from datetime import date

from app.services.crossword import algorithmic_fallback_puzzle, legacy_fallback_puzzle, validate_and_normalize


def test_fallback_is_public_11_by_11_and_fully_clued():
    puzzle = algorithmic_fallback_puzzle(date(2026, 8, 9))
    assert len(puzzle["grid"]) == 11
    assert all(len(row) == 11 for row in puzzle["grid"])
    assert all(set(row) <= {"#", "."} for row in puzzle["grid"])
    assert len(puzzle["clues"]) >= 8
    assert all(clue["length"] >= 3 for clue in puzzle["clues"])
    across = {
        "".join(puzzle["solution"][clue["row"]][clue["col"] + i] for i in range(clue["length"]))
        for clue in puzzle["clues"] if clue["direction"] == "across"
    }
    down = {
        "".join(puzzle["solution"][clue["row"] + i][clue["col"]] for i in range(clue["length"]))
        for clue in puzzle["clues"] if clue["direction"] == "down"
    }
    assert across != down


def test_validator_rejects_asymmetric_blocks():
    puzzle = legacy_fallback_puzzle()
    rows = list(puzzle["solution"])
    # "#" for the blocks, not ".". The grid-character check runs BEFORE the
    # symmetry check, so a "." row failed with "Grid may contain only A-Z
    # and #" and this test never reached the rule it names — the symmetry
    # check had no coverage at all while appearing to have some.
    rows[0] = "A##########"
    with pytest.raises(ValueError, match="symmetry"):
        validate_and_normalize({"rows": rows, "clues": puzzle["clues"]})


def test_validator_rejects_missing_clue():
    puzzle = legacy_fallback_puzzle()
    with pytest.raises(ValueError, match="Missing clue"):
        validate_and_normalize({"rows": puzzle["solution"], "clues": puzzle["clues"][:-1]})


def test_answers_are_not_present_in_public_grid():
    puzzle = algorithmic_fallback_puzzle(date(2026, 8, 9))
    assert "HEART" not in "".join(puzzle["grid"])


def test_different_dates_produce_different_shapes_or_answers():
    first = algorithmic_fallback_puzzle(date(2026, 8, 9))
    second = algorithmic_fallback_puzzle(date(2026, 8, 10))
    assert first["solution"] != second["solution"]
