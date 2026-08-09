import pytest

from app.services.crossword import fallback_puzzle, validate_and_normalize


def test_fallback_is_public_11_by_11_and_fully_clued():
    puzzle = fallback_puzzle()
    assert len(puzzle["grid"]) == 11
    assert all(len(row) == 11 for row in puzzle["grid"])
    assert all(set(row) <= {"#", "."} for row in puzzle["grid"])
    assert len(puzzle["clues"]) == 10
    assert all(clue["length"] >= 3 for clue in puzzle["clues"])
    assert puzzle["solution"][3] == "###HEART###"


def test_validator_rejects_asymmetric_blocks():
    puzzle = fallback_puzzle()
    rows = list(puzzle["solution"])
    rows[0] = ".##########"
    with pytest.raises(ValueError, match="symmetry"):
        validate_and_normalize({"rows": rows, "clues": puzzle["clues"]})


def test_validator_rejects_missing_clue():
    puzzle = fallback_puzzle()
    with pytest.raises(ValueError, match="Missing clue"):
        validate_and_normalize({"rows": puzzle["solution"], "clues": puzzle["clues"][:-1]})


def test_answers_are_not_present_in_public_grid():
    puzzle = fallback_puzzle()
    assert "HEART" not in "".join(puzzle["grid"])
