from datetime import date

from app.services.editorial_features import (
    WORDS,
    _ai_word,
    _unused_curated_word,
    word_key,
)


def test_word_key_collapses_shared_root():
    """SERENDIPITOUS and SERENDIPITY shipped three days apart because the old
    avoid-list compared exact strings — they must collide now."""
    assert word_key("SERENDIPITY") == word_key("serendipitous")


def test_word_key_keeps_unrelated_words_apart():
    assert word_key("RESILIENT") != word_key("RESIDUAL")
    assert word_key("ELOQUENT") != word_key("EQUANIMITY")


def test_curated_fallback_skips_used_words():
    used = {word_key(WORDS[0]["word"]), word_key(WORDS[1]["word"])}
    chosen = _unused_curated_word(0, used)
    assert word_key(chosen["word"]) not in used


def test_curated_fallback_returns_something_when_bank_exhausted():
    used = {word_key(entry["word"]) for entry in WORDS}
    assert _unused_curated_word(3, used) in WORDS


async def test_ai_word_retries_past_a_repeat(monkeypatch):
    replies = [
        {"word": "SERENDIPITY", "pronunciation": "p", "part_of_speech": "noun",
         "definition": "d", "example": "e", "origin": "o"},
        {"word": "QUOTIDIAN", "pronunciation": "p", "part_of_speech": "adjective",
         "definition": "d", "example": "e", "origin": "o"},
    ]
    calls = []

    async def fake_claude(*, system, user_content, max_tokens, temperature):
        calls.append(user_content)
        return replies[len(calls) - 1]

    monkeypatch.setattr("app.services.editorial_features.call_claude_json", fake_claude)
    word = await _ai_word(date(2026, 9, 4), [], {word_key("SERENDIPITOUS")})

    assert word["word"] == "QUOTIDIAN"
    assert len(calls) == 2
    # The retry must name the rejected word, or it just re-rolls the same prompt.
    assert "SERENDIPITY" in calls[1]


async def test_ai_word_gives_up_when_every_attempt_repeats(monkeypatch):
    async def fake_claude(*, system, user_content, max_tokens, temperature):
        return {"word": "SERENDIPITY", "pronunciation": "p", "part_of_speech": "noun",
                "definition": "d", "example": "e", "origin": "o"}

    monkeypatch.setattr("app.services.editorial_features.call_claude_json", fake_claude)
    assert await _ai_word(date(2026, 9, 4), [], {word_key("SERENDIPITY")}) is None


def test_author_key_collapses_name_variants():
    from app.services.editorial_features import author_key
    assert author_key("Albert Einstein") == author_key("A. Einstein") == author_key("Dr. Albert Einstein")
    assert author_key("Maya Angelou") != author_key("Albert Einstein")


def test_quote_key_ignores_formatting():
    from app.services.editorial_features import quote_key
    assert quote_key('"Be the change you wish to see."') == quote_key("be the change  you wish to see")


async def test_quote_draw_discards_used_text_and_caps_draws(monkeypatch):
    from app.services import editorial_features as ef
    draws = []

    async def fake_call(_endpoint, *a, **k):
        draws.append(1)
        return {"quote": "Same old line", "author": "Someone New"}

    monkeypatch.setattr(ef, "call_apiverve", fake_call)
    result = await ef._apiverve_quote([], {ef.quote_key("Same old line")})
    assert result is None
    assert len(draws) == ef.QUOTE_MAX_DRAWS


async def test_quote_draw_accepts_recent_author_when_text_is_new(monkeypatch):
    from app.services import editorial_features as ef

    async def fake_call(_endpoint, *a, **k):
        return {"quote": "A brand new line", "author": "Albert Einstein"}

    monkeypatch.setattr(ef, "call_apiverve", fake_call)
    result = await ef._apiverve_quote(["A. Einstein"], set())
    assert result == {"quote": "A brand new line", "author": "Albert Einstein"}
