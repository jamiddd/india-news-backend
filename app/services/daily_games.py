from __future__ import annotations

import asyncio
import logging
import re
from datetime import date

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DailyQuiz, DailySpellingBee, DailyWordLadder
from app.services import wordlists
from app.services.apiverve_client import call_apiverve

logger = logging.getLogger(__name__)

# Same content filter used for the daily poll (app/services/polls.py) — keep
# game content (quiz questions especially) away from sensitive subjects.
UNSAFE = re.compile(r"\b(killed|dies|death|rape|murder|lynch|riot|communal|terror|suicide|victim|accident|crash|flood|cyclone|earthquake|funeral|hostage)\b", re.I)

SPELLING_BEES = [
    # Each word list must include at least one pangram (a word using every
    # letter at least once) — the client scores/celebrates pangrams, so a
    # puzzle without one is unwinnable for that part of the game. Used as the
    # deterministic fallback when AI generation fails or fails validation.
    (list("AELPRST"), "A", ["ALERT", "ALTER", "APPEAL", "APPLE", "LATER", "LASER", "PEAR", "PLATE", "PLEAT", "RATE", "REAL", "SEAL", "SPARE", "SPEAR", "STAPLE", "STAR", "START", "STEAL", "TART", "PLASTER"]),
    (list("AEGINRT"), "E", ["EAGER", "EARN", "EARRING", "EATING", "ENGINE", "ENTER", "ENTIRE", "GREEN", "INNER", "INTER", "RANGE", "REGAIN", "RENT", "RETAIN", "TEAR", "TREE", "GRANITE"]),
    (list("ACILMOT"), "O", ["ATOM", "COAL", "COAT", "COIL", "COLA", "COMIC", "COOL", "LOOM", "LOOT", "MOAT", "TACO", "TOOL", "TOTAL", "COMITAL"]),
    (list("DEINOST"), "I", ["DIET", "DINE", "DINO", "EDIT", "INSIDE", "INTEND", "NOISE", "ONION", "SITE", "TIDE", "TINE", "SEDITION"]),
    (list("ABCEKLR"), "R", ["BAKER", "BARK", "BEAR", "BRAKE", "BREAK", "CARE", "CLEAR", "CRACK", "CREAK", "RACE", "RACK", "RARE", "REAL", "REBEL", "REEK", "BLACKER"]),
]

WORD_LADDERS = [
    ("COLD", "WARM", ["COLD", "CORD", "CARD", "WARD", "WARM", "WORD", "WORE", "CORE", "CARE", "WAVE"], 4),
    ("HEAD", "TAIL", ["HEAD", "HEAL", "TEAL", "TELL", "TALL", "TAIL", "HEAT", "HAIL", "HALL", "TEAM"], 5),
    ("LEAD", "GOLD", ["LEAD", "LOAD", "GOAD", "GOLD", "LORD", "ROAD", "READ", "BEAD", "BOLD", "COLD"], 3),
    ("FOUR", "FIVE", ["FOUR", "FOUL", "FOIL", "FAIL", "FALL", "FILL", "FILE", "FIVE", "FIRE", "FINE", "FIRM"], 7),
    # optimal_steps was 6, describing the intended SAME-CAME-CAMP-COMP-COOP-
    # COOT-COST route. But CASE and CAST are in the allowed list as
    # distractors, and they open a shorter one:
    #     SAME -> CAME -> CASE -> CAST -> COST
    # _validate_ladder's BFS re-derives the true optimum and already
    # overrode this to 4 at runtime (logging a warning every time), so the
    # constant was the only thing that was wrong. Corrected rather than
    # removing the distractors, which would change the puzzle's design.
    ("SAME", "COST", ["SAME", "CAME", "CAMP", "COMP", "COOP", "COOT", "COST", "COME", "CAST", "CASE", "MOST"], 4),
]

QUIZ_SETS = [
    [
        ("Which planet is known as the Red Planet?", ["Venus", "Mars", "Jupiter", "Mercury"], 1, "Iron minerals in its soil give Mars its reddish appearance."),
        ("Who wrote 'The Discovery of India'?", ["Mahatma Gandhi", "Rabindranath Tagore", "Jawaharlal Nehru", "B. R. Ambedkar"], 2, "Jawaharlal Nehru wrote it while imprisoned in 1942–1945."),
        ("What is the chemical symbol for gold?", ["Ag", "Au", "Gd", "Go"], 1, "Au comes from the Latin word aurum."),
        ("Which ocean is the largest?", ["Atlantic", "Indian", "Arctic", "Pacific"], 3, "The Pacific covers more area than all land combined."),
        ("How many players does a cricket team field?", ["9", "10", "11", "12"], 2, "A cricket side consists of eleven players."),
    ],
    [
        ("What is the capital of Australia?", ["Sydney", "Melbourne", "Canberra", "Perth"], 2, "Canberra was selected as a compromise between Sydney and Melbourne."),
        ("Which gas is most abundant in Earth's atmosphere?", ["Oxygen", "Nitrogen", "Carbon dioxide", "Argon"], 1, "Nitrogen makes up about 78 percent of the atmosphere."),
        ("The Ajanta Caves are in which Indian state?", ["Gujarat", "Maharashtra", "Odisha", "Bihar"], 1, "The UNESCO-listed caves are in Maharashtra."),
        ("What does CPU stand for?", ["Central Processing Unit", "Computer Power Utility", "Core Program Unit", "Central Program User"], 0, "CPU means Central Processing Unit."),
        ("Which animal is the largest mammal?", ["African elephant", "Blue whale", "Giraffe", "Whale shark"], 1, "The blue whale is the largest known animal."),
    ],
    [
        ("Which river flows through Egypt?", ["Nile", "Amazon", "Danube", "Yangtze"], 0, "The Nile has supported Egyptian civilization for millennia."),
        ("Who developed the theory of general relativity?", ["Isaac Newton", "Marie Curie", "Albert Einstein", "Niels Bohr"], 2, "Einstein published general relativity in 1915."),
        ("What is India's national flower?", ["Rose", "Lotus", "Jasmine", "Marigold"], 1, "The lotus is India's national flower."),
        ("Which is the smallest prime number?", ["0", "1", "2", "3"], 2, "Two is the first and only even prime."),
        ("In which sport is the term 'love' used?", ["Golf", "Tennis", "Hockey", "Chess"], 1, "In tennis, love represents a score of zero."),
    ],
]


def _fallback_bee(puzzle_date: date) -> tuple[list[str], str, list[str]]:
    return SPELLING_BEES[puzzle_date.toordinal() % len(SPELLING_BEES)]


def _fallback_ladder(puzzle_date: date) -> tuple[str, str, list[str], int]:
    return WORD_LADDERS[puzzle_date.toordinal() % len(WORD_LADDERS)]


def _fallback_quiz_questions(puzzle_date: date) -> list[dict]:
    return [
        {"id": index + 1, "question": question, "options": options, "correct_index": correct, "explanation": explanation}
        for index, (question, options, correct, explanation) in enumerate(QUIZ_SETS[puzzle_date.toordinal() % len(QUIZ_SETS)])
    ]


# ---------------------------------------------------------------------------
# Spelling Bee
# ---------------------------------------------------------------------------

def _validate_bee(payload: dict) -> tuple[list[str], str, list[str]]:
    letters = [str(letter).strip().upper() for letter in payload.get("letters") or []]
    center = str(payload.get("center_letter") or "").strip().upper()
    words = [str(word).strip().upper() for word in payload.get("words") or []]
    if len(letters) != 7 or len(set(letters)) != 7 or any(len(letter) != 1 or not letter.isalpha() for letter in letters):
        raise ValueError("Need exactly 7 unique letters")
    if center not in letters:
        raise ValueError("Center letter must be one of the 7 letters")
    # Upper bound matches BEE_MAX_WORDS in scripts/build_wordlists.py. A
    # richer word list makes a better puzzle; 20 was only ever what an LLM
    # could be relied on to produce in one response, and 40 was the ceiling
    # while accepted words came from the same SCOWL level as the letters.
    if not (8 <= len(words) <= 80) or len(set(words)) != len(words):
        raise ValueError("Need 8-80 unique words")
    letter_set = set(letters)
    for word in words:
        if len(word) < 4 or not word.isalpha():
            raise ValueError(f"Invalid word: {word}")
        if center not in word:
            raise ValueError(f"Word missing center letter: {word}")
        if not set(word) <= letter_set:
            raise ValueError(f"Word uses letters outside the 7: {word}")
    if not any(set(word) == letter_set for word in words):
        raise ValueError("Word list has no true pangram (all 7 letters used)")
    return letters, center, words


async def generate_spelling_bee(puzzle_date: date) -> tuple[list[str], str, list[str], str]:
    """Straight from the committed SCOWL word lists.

    The APIVerve call this replaced could never work: `words[]` is a
    Premium-only field, so the free tier returns letters with no valid-word
    list, which isn't a playable puzzle. Confirmed again 2026-09-03 —
    `"words": null` on every response. Dropping it also gives the games that
    DO get APIVerve data (crossword, word search, quiz) back one request a
    day against a shared rate limit that has produced 429s in production.

    wordlists picks from ~3,000 puzzles that were proven playable at build
    time, so unlike the old AI path there is nothing here to fail — the
    curated bank below only covers the data files not shipping at all.
    """
    if wordlists.available():
        try:
            letters, center, words = wordlists.spelling_bee_for(puzzle_date)
            # Same shape check the old sources went through — the puzzle was
            # verified at build time, so a failure here means the data files
            # and this code have drifted apart, which is worth a loud log.
            letters, center, words = _validate_bee(
                {"letters": letters, "center_letter": center, "words": words}
            )
            return letters, center, words, "wordlist"
        except Exception as exc:
            logger.error("Committed spelling bee list failed validation: %s", exc)
    letters, center, words = _fallback_bee(puzzle_date)
    return letters, center, words, "curated"


# ---------------------------------------------------------------------------
# Word Ladder
# ---------------------------------------------------------------------------

def _shortest_ladder_steps(start: str, target: str, pool: set[str]) -> Optional[int]:
    """BFS over the one-letter-change graph induced by `pool` (which must
    already include start/target) — returns the TRUE minimum number of steps
    from start to target, or None if no path exists at all.

    Deliberately does not take a max_steps cap and stop early: an earlier
    version (_reachable_in_steps) only checked "is target reachable within
    the LLM's claimed optimal_steps," which a shorter real path still
    satisfies — e.g. a claimed optimal_steps=5 passed validation even though
    COLD -> CORD -> CARD -> WARD -> WARM is a real 4-step solution in the
    same pool, silently mislabeling the puzzle's difficulty. Since a real
    shortest path can't exceed the pool size + 1, BFS naturally terminates
    well before that regardless."""
    if start == target:
        return 0
    visited = {start}
    frontier = {start}
    steps = 0
    while frontier:
        steps += 1
        next_frontier = set()
        for prev in frontier:
            for word in pool:
                if word in visited:
                    continue
                if len(word) == len(prev) and sum(a != b for a, b in zip(word, prev)) == 1:
                    if word == target:
                        return steps
                    next_frontier.add(word)
        visited |= next_frontier
        frontier = next_frontier
    return None


def _validate_ladder(payload: dict) -> tuple[str, str, list[str], int]:
    start = str(payload.get("start_word") or "").strip().upper()
    target = str(payload.get("target_word") or "").strip().upper()
    allowed = [str(word).strip().upper() for word in payload.get("allowed_words") or []]
    claimed_optimal = payload.get("optimal_steps")
    length = len(start)
    if length < 3 or len(target) != length:
        raise ValueError("Start/target must be same-length words of at least 3 letters")
    if not start.isalpha() or not target.isalpha() or start == target:
        raise ValueError("Start and target must be distinct alphabetic words")
    # Upper bound is generous because allowed_words is a hidden whitelist,
    # not a displayed word bank: the client rejects anything outside it
    # without ever showing the player what it contains, so a short list just
    # refuses legitimate words. wordlists passes the entire 4-letter pool
    # (~1,745 words, ~14 KB of JSON).
    if not (6 <= len(allowed) <= 5000) or len(set(allowed)) != len(allowed):
        raise ValueError("Need 6-5000 unique allowed words")
    if any(len(word) != length or not word.isalpha() for word in allowed):
        raise ValueError("Allowed words must all be alphabetic and same length as start/target")
    if not isinstance(claimed_optimal, int) or not (1 <= claimed_optimal <= len(allowed) + 1):
        raise ValueError("optimal_steps out of range")
    true_optimal = _shortest_ladder_steps(start, target, set(allowed) | {start, target})
    if true_optimal is None:
        raise ValueError(f"No path from {start} to {target} exists using the allowed words")
    if true_optimal != claimed_optimal:
        # Trust the graph, not the LLM's claim — a shorter real path here
        # means the LLM's stated optimal_steps was simply wrong, not that
        # the puzzle is unplayable. Log so a persistent pattern (the model
        # consistently overestimating) is visible without blocking today's
        # puzzle over a labeling mismatch.
        logger.warning(
            "Word Ladder AI claimed optimal_steps=%s but the real shortest path is %s (%s -> %s)",
            claimed_optimal, true_optimal, start, target,
        )
    return start, target, allowed, true_optimal


async def generate_word_ladder(puzzle_date: date) -> tuple[str, str, list[str], int, str]:
    """Straight from the committed SCOWL word lists.

    Replaces both the APIVerve call (whose `solution` word pool is
    Premium-only — re-confirmed 2026-09-03 as `null` on every response) and,
    before it, an LLM that returned COLD -> WARM on 8 of 14 days because it
    is the textbook example of a word ladder.

    Pairs come from the largest connected component of the 4-letter pool with
    a verified 3-6 step path, so _validate_ladder's BFS re-derivation here is
    a consistency check rather than a gate.
    """
    if wordlists.available():
        try:
            start, target, allowed, steps = wordlists.word_ladder_for(puzzle_date)
            start, target, allowed, optimal = _validate_ladder({
                "start_word": start,
                "target_word": target,
                "allowed_words": allowed,
                "optimal_steps": steps,
            })
            return start, target, allowed, optimal, "wordlist"
        except Exception as exc:
            logger.error("Committed word ladder list failed validation: %s", exc)
    start, target, allowed, optimal = _fallback_ladder(puzzle_date)
    return start, target, allowed, optimal, "curated"


# ---------------------------------------------------------------------------
# Daily Quiz
# ---------------------------------------------------------------------------

def _validate_quiz(payload: dict) -> list[dict]:
    """General-purpose quiz-shape validator — exactly 5 distinct questions,
    each with 4 distinct options and a valid correct_index, filtered for
    sensitive topics. Not on the APIVerve path (see _parse_trivia_question,
    which validates one question at a time from a different payload shape)
    but kept as the shape check for QUIZ_SETS and any future batch source."""
    questions = payload.get("questions") or []
    if len(questions) != 5:
        raise ValueError("Need exactly 5 questions")
    seen_questions: set[str] = set()
    result = []
    for index, item in enumerate(questions):
        question = str(item.get("question") or "").strip()
        options = [str(option).strip() for option in item.get("options") or []]
        correct = item.get("correct_index")
        explanation = str(item.get("explanation") or "").strip()
        if not question or question.casefold() in seen_questions:
            raise ValueError("Missing or duplicate question")
        seen_questions.add(question.casefold())
        if len(options) != 4 or len({o.casefold() for o in options}) != 4:
            raise ValueError("Need exactly 4 distinct options")
        if not isinstance(correct, int) or not (0 <= correct < 4):
            raise ValueError("correct_index out of range")
        if UNSAFE.search(question):
            raise ValueError("Unsafe question content")
        result.append({"id": index + 1, "question": question, "options": options, "correct_index": correct, "explanation": explanation})
    return result


_OPTION_LABEL_RE = re.compile(r"^[A-Za-z]\s+")


def _parse_trivia_question(data: dict) -> dict | None:
    """One APIVerve /trivia response -> our question shape, or None if it
    doesn't fit (e.g. a true/false question with only 2 options — the
    client's quiz UI is a fixed 4-option layout)."""
    question = str(data.get("question") or "").strip()
    answer = str(data.get("answer") or "").strip().casefold()
    raw_options = [str(option).strip() for option in data.get("options") or []]
    # Options can come back letter-prefixed, e.g. "A Yes" — strip that label
    # before comparing against `answer` or displaying to the user.
    options = [_OPTION_LABEL_RE.sub("", option).strip() for option in raw_options]
    if not question or len(options) != 4 or len({o.casefold() for o in options}) != 4:
        return None
    correct = next((i for i, o in enumerate(options) if o.casefold() == answer), None)
    if correct is None or UNSAFE.search(question):
        return None
    return {"question": question, "options": options, "correct_index": correct, "explanation": ""}


async def _apiverve_quiz() -> list[dict] | None:
    seen_questions: set[str] = set()
    questions: list[dict] = []
    # Free-tier trivia questions are single, unrelated draws with no
    # built-in "give me 5" batch mode, and not every draw fits our fixed
    # 4-option layout (see _parse_trivia_question) — so over-fetch and keep
    # the first 5 that validate, capped so a bad run can't loop forever.
    # Spaced out — firing all 15 back-to-back trips APIVerve's rate limit
    # (429) well before the monthly credit cap.
    for attempt in range(15):
        if len(questions) >= 5:
            break
        if attempt > 0:
            await asyncio.sleep(0.5)
        data = await call_apiverve("trivia", {"category": "general"})
        if data is None:
            continue
        parsed = _parse_trivia_question(data)
        if parsed is None or parsed["question"].casefold() in seen_questions:
            continue
        seen_questions.add(parsed["question"].casefold())
        questions.append(parsed)
    if len(questions) < 5:
        return None
    return [{"id": index + 1, **item} for index, item in enumerate(questions[:5])]


async def generate_quiz(puzzle_date: date) -> tuple[list[dict], str]:
    apiverve_questions = await _apiverve_quiz()
    if apiverve_questions is not None:
        return apiverve_questions, "apiverve"
    return _fallback_quiz_questions(puzzle_date), "curated"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

async def get_or_create_daily_games(session: AsyncSession, puzzle_date: date):
    lock_key = 76000000 + int(puzzle_date.strftime("%Y%m%d"))
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})

    bee = (await session.execute(select(DailySpellingBee).where(DailySpellingBee.puzzle_date == puzzle_date))).scalar_one_or_none()
    if bee is None:
        letters, center, words, source = await generate_spelling_bee(puzzle_date)
        bee = DailySpellingBee(puzzle_date=puzzle_date, letters=letters, center_letter=center, words=words, source=source)
        session.add(bee)

    ladder = (await session.execute(select(DailyWordLadder).where(DailyWordLadder.puzzle_date == puzzle_date))).scalar_one_or_none()
    if ladder is None:
        start, target, allowed, optimal, source = await generate_word_ladder(puzzle_date)
        ladder = DailyWordLadder(puzzle_date=puzzle_date, start_word=start, target_word=target, allowed_words=allowed, optimal_steps=optimal, source=source)
        session.add(ladder)

    quiz = (await session.execute(select(DailyQuiz).where(DailyQuiz.puzzle_date == puzzle_date))).scalar_one_or_none()
    if quiz is None:
        questions, source = await generate_quiz(puzzle_date)
        quiz = DailyQuiz(puzzle_date=puzzle_date, questions=questions, source=source)
        session.add(quiz)

    await session.commit()
    return bee, ladder, quiz
