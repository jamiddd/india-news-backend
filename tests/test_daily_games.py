from datetime import date, timedelta

import pytest

from app.services.daily_games import (
    QUIZ_SETS,
    SPELLING_BEES,
    WORD_LADDERS,
    _validate_bee,
    _validate_ladder,
    _validate_quiz,
    WORDLE_FALLBACKS,
    WORDLE_LENGTH,
    _validate_wordle,
    generate_spelling_bee,
    generate_word_ladder,
    generate_wordle,
)
from app.services import wordlists


def test_spelling_bee_words_use_only_letters_and_center():
    for letters, center, words in SPELLING_BEES:
        allowed = set(letters)
        assert len(allowed) == 7 and center in allowed
        assert all(len(word) >= 4 and center in word and set(word) <= allowed for word in words)


def test_word_ladder_targets_are_reachable_in_advertised_steps():
    for start, target, allowed, optimal in WORD_LADDERS:
        reached = {start}
        for _ in range(optimal):
            reached |= {
                word for word in allowed
                if any(sum(a != b for a, b in zip(word, previous)) == 1 for previous in reached)
            }
        assert target in reached


def test_quiz_sets_have_five_valid_questions():
    for questions in QUIZ_SETS:
        assert len(questions) == 5
        assert all(options and 0 <= correct < len(options) and explanation for _, options, correct, explanation in questions)


def test_bee_validator_accepts_curated_bank_entries():
    for letters, center, words in SPELLING_BEES:
        validated_letters, validated_center, validated_words = _validate_bee(
            {"letters": letters, "center_letter": center, "words": words}
        )
        assert set(validated_letters) == set(letters)
        assert validated_center == center


def test_bee_validator_rejects_missing_pangram():
    words = ["ALERT", "ALTER", "APPEAL", "APPLE", "LATER", "LASER", "PEAR", "PLATE"]
    with pytest.raises(ValueError, match="pangram"):
        _validate_bee({"letters": list("AELPRST"), "center_letter": "A", "words": words})


def test_bee_validator_rejects_word_outside_letters():
    words = ["ALERT", "ALTER", "APPEAL", "APPLE", "LATER", "LASER", "PEAR", "ZEBRA"]
    with pytest.raises(ValueError, match="outside"):
        _validate_bee({"letters": list("AELPRST"), "center_letter": "A", "words": words})


def test_ladder_validator_accepts_curated_bank_entries():
    for start, target, allowed, optimal in WORD_LADDERS:
        validated_start, validated_target, validated_allowed, validated_optimal = _validate_ladder(
            {"start_word": start, "target_word": target, "allowed_words": allowed, "optimal_steps": optimal}
        )
        assert validated_start == start
        assert validated_target == target
        assert validated_optimal == optimal


def test_ladder_validator_rejects_unreachable_target():
    with pytest.raises(ValueError, match="No path"):
        _validate_ladder({
            "start_word": "COLD", "target_word": "WARM",
            "allowed_words": ["BOLD", "GOLD", "MOLD", "SOLD", "TOLD", "HOLD"],
            "optimal_steps": 3,
        })


def test_ladder_validator_rejects_mismatched_lengths():
    with pytest.raises(ValueError, match="same length"):
        _validate_ladder({
            "start_word": "COLD", "target_word": "WARM",
            "allowed_words": ["COL", "COLT", "CORD", "CARD", "WARD", "WARN"],
            "optimal_steps": 4,
        })


def _valid_quiz_payload():
    return {
        "questions": [
            {"question": f"Question {i}?", "options": ["A", "B", "C", "D"], "correct_index": 0, "explanation": "Because."}
            for i in range(5)
        ]
    }


def test_quiz_validator_accepts_well_formed_payload():
    questions = _validate_quiz(_valid_quiz_payload())
    assert len(questions) == 5
    assert all(q["correct_index"] == 0 for q in questions)


def test_quiz_validator_rejects_wrong_question_count():
    payload = _valid_quiz_payload()
    payload["questions"] = payload["questions"][:4]
    with pytest.raises(ValueError, match="5 questions"):
        _validate_quiz(payload)


def test_quiz_validator_rejects_out_of_range_correct_index():
    payload = _valid_quiz_payload()
    payload["questions"][0]["correct_index"] = 7
    with pytest.raises(ValueError, match="correct_index"):
        _validate_quiz(payload)


def test_quiz_validator_rejects_unsafe_question():
    payload = _valid_quiz_payload()
    payload["questions"][0]["question"] = "How many people were killed in the crash?"
    with pytest.raises(ValueError, match="Unsafe"):
        _validate_quiz(payload)


class TestWordlistBackedPuzzles:
    """Spelling Bee and Word Ladder now come from the committed SCOWL lists
    (app/data/wordlists), which are enumerated at build time so that only
    playable puzzles exist. These guard the wiring; tests/test_wordlists.py
    covers the data files themselves."""

    async def test_spelling_bee_comes_from_the_wordlist(self):
        letters, centre, words, source = await generate_spelling_bee(date(2026, 9, 4))
        assert source == "wordlist"
        assert len(letters) == 7 and centre in letters
        assert any(set(word) == set(letters) for word in words), "puzzle has no pangram"
        assert all(centre in word and set(word) <= set(letters) for word in words)

    async def test_word_ladder_comes_from_the_wordlist(self):
        start, target, allowed, optimal, source = await generate_word_ladder(date(2026, 9, 4))
        assert source == "wordlist"
        assert start != target and len(start) == len(target)
        assert optimal >= 1
        # The whole pool, not a shortlist: the client uses allowed_words as a
        # hidden whitelist, so anything smaller rejects real words the player
        # can't know are excluded.
        assert len(allowed) > 1000
        assert target in allowed

    async def test_ladder_accepts_ordinary_off_path_words(self):
        """Regression: FIGS -> FINS -> FINE was rejected on 2026-09-03 because
        allowed_words had been trimmed to 12 entries, even though all three are
        real words a letter apart and FINE -> FILE -> FILM finishes."""
        _, _, allowed, _, _ = await generate_word_ladder(date(2026, 9, 3))
        for word in ("FIGS", "FINS", "FINE", "FILE", "FILM", "FIRS", "FIRM"):
            assert word in allowed, word

    async def test_consecutive_days_differ(self):
        """The old LLM path returned COLD -> WARM on 8 of 14 days."""
        bees, ladders = set(), set()
        for offset in range(30):
            day = date(2026, 9, 4) + timedelta(days=offset)
            letters, _, _, _ = await generate_spelling_bee(day)
            start, target, _, _, _ = await generate_word_ladder(day)
            bees.add("".join(letters))
            ladders.add((start, target))
        assert len(bees) == 30
        assert len(ladders) == 30

    async def test_wordle_comes_from_the_wordlist(self):
        answer, source = await generate_wordle(date(2026, 9, 4))
        assert source == "wordlist"
        assert len(answer) == WORDLE_LENGTH and answer.isalpha() and answer.isupper()

    async def test_wordle_answer_is_always_typeable(self):
        """The client validates guesses against accepted_guesses. An answer
        outside that set would make the solution impossible to enter."""
        accepted = set(wordlists.accepted_guesses())
        for offset in range(60):
            answer, _ = await generate_wordle(date(2026, 9, 4) + timedelta(days=offset))
            assert answer in accepted, answer

    async def test_wordle_consecutive_days_are_not_alphabetical_neighbours(self):
        """wordle_answers.txt is written sorted, unlike the shuffled bee and
        ladder files, so a +1 daily walk would leak the next day's answer to
        anyone who plays daily. wordlists.WORDLE_STRIDE is what prevents it."""
        answers = [
            (await generate_wordle(date(2026, 9, 4) + timedelta(days=offset)))[0]
            for offset in range(30)
        ]
        assert len(set(answers)) == 30
        assert not any(a[:3] == b[:3] for a, b in zip(answers, answers[1:]))


def test_wordle_walk_covers_the_whole_pool_before_repeating():
    """The stride must stay coprime with the pool size across rebuilds --
    a shared factor would silently shrink the rotation to a fraction of it."""
    pool_size = len(wordlists._wordle_answers())
    seen = {wordlists.wordle_for(date.fromordinal(o))[0] for o in range(739_000, 739_000 + pool_size)}
    assert len(seen) == pool_size


def test_wordle_fallbacks_are_valid_answers():
    accepted = wordlists.accepted_guesses()
    for word in WORDLE_FALLBACKS:
        assert _validate_wordle(word, accepted) == word


def test_validate_wordle_rejects_untypeable_answers():
    with pytest.raises(ValueError):
        _validate_wordle("CRANES", wordlists.accepted_guesses())
    with pytest.raises(ValueError):
        _validate_wordle("ZZZZZ", wordlists.accepted_guesses())
