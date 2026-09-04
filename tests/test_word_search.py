from datetime import date, timedelta

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


def test_every_theme_is_well_formed():
    """The bank grew from 7 to 140 themes; these are the invariants
    place_words and the client layout depend on."""
    names = [name for name, _ in THEMES]
    assert len(names) == len(set(names)), "duplicate theme names"
    for name, words in THEMES:
        assert len(words) == 10, f"{name}: expected 10 words, got {len(words)}"
        assert len(set(words)) == 10, f"{name}: duplicate words"
        for word in words:
            assert word.isalpha() and word.isupper(), f"{name}: {word!r} is not plain A-Z"
            assert 3 <= len(word) <= 12, f"{name}: {word!r} does not fit a 12x12 grid"


def test_every_theme_packs_on_every_date_it_can_be_used():
    """Placement is seeded by the date, so a theme that packs on one day can
    fail on another — and place_words raises RuntimeError with no fallback in
    the unattended nightly job. Walk enough consecutive dates that every theme
    comes up, and pack each one for real."""
    seen = set()
    day = date(2026, 9, 5)
    for offset in range(len(THEMES) * 4):
        current = day + timedelta(days=offset)
        theme, words = theme_and_words_for_date(current)
        rows, placed = place_words(current, words)
        assert len(rows) == 12 and sorted(placed) == sorted(words), theme
        seen.add(theme)
    assert len(seen) > len(THEMES) // 2, "date walk did not exercise enough themes"
