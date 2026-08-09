from datetime import date

from app.services.word_search import generate_daily_word_search


def _directions():
    return ((0, 1), (1, 0), (1, 1), (1, -1), (0, -1), (-1, 0), (-1, -1), (-1, 1))


def test_daily_word_search_is_deterministic_and_contains_every_word():
    generated = generate_daily_word_search(date(2026, 8, 9))
    assert generated == generate_daily_word_search(date(2026, 8, 9))
    _, rows, words = generated
    assert len(rows) == 12 and all(len(row) == 12 for row in rows)
    for word in words:
        assert any(
            0 <= row + (len(word) - 1) * dr < 12
            and 0 <= col + (len(word) - 1) * dc < 12
            and "".join(rows[row + i * dr][col + i * dc] for i in range(len(word))) == word
            for row in range(12)
            for col in range(12)
            for dr, dc in _directions()
        )


def test_different_dates_produce_different_grids():
    assert generate_daily_word_search(date(2026, 8, 9)) != generate_daily_word_search(date(2026, 8, 10))
