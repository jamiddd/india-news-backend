from app.services.daily_games import QUIZ_SETS, SPELLING_BEES, WORD_LADDERS


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
