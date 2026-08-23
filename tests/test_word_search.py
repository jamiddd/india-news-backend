from datetime import date

import pytest

from app.services.word_search import THEMES, _validate_theme_and_words, place_words


def _directions():
    return ((0, 1), (1, 0), (1, 1), (1, -1), (0, -1), (-1, 0), (-1, -1), (-1, 1))


def _theme_words():
    return THEMES[0][1]


def test_daily_word_search_is_deterministic_and_contains_every_word():
    words = _theme_words()
    generated = place_words(date(2026, 8, 9), words)
    assert generated == place_words(date(2026, 8, 9), words)
    rows, placed = generated
    assert len(rows) == 12 and all(len(row) == 12 for row in rows)
    for word in placed:
        assert any(
            0 <= row + (len(word) - 1) * dr < 12
            and 0 <= col + (len(word) - 1) * dc < 12
            and "".join(rows[row + i * dr][col + i * dc] for i in range(len(word))) == word
            for row in range(12)
            for col in range(12)
            for dr, dc in _directions()
        )


def test_different_dates_produce_different_grids():
    words = _theme_words()
    assert place_words(date(2026, 8, 9), words) != place_words(date(2026, 8, 10), words)


def test_theme_validator_accepts_curated_bank_entries():
    for theme, words in THEMES:
        validated_theme, validated_words = _validate_theme_and_words({"theme": theme, "words": words})
        assert validated_theme == theme
        assert sorted(validated_words) == sorted(w.upper() for w in words)


def test_theme_validator_rejects_wrong_word_count():
    with pytest.raises(ValueError, match="10 unique"):
        _validate_theme_and_words({"theme": "Space", "words": ["GALAXY", "COMET"]})


def test_theme_validator_rejects_non_alpha_or_bad_length():
    words = ["GALAXY", "PLANET", "COMET", "ORBIT", "NEBULA", "ROCKET", "SATURN", "LUNAR", "METEOR", "AB12"]
    with pytest.raises(ValueError, match="4-9 letters"):
        _validate_theme_and_words({"theme": "Space", "words": words})
