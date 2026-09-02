from datetime import date

from app.services.word_search import THEMES, place_words, theme_and_words_for_date


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


def test_theme_and_words_for_date_is_deterministic_and_from_the_curated_bank():
    theme, words = theme_and_words_for_date(date(2026, 8, 9))
    assert (theme, words) == theme_and_words_for_date(date(2026, 8, 9))
    assert any(theme == t and words == list(w) for t, w in THEMES)
