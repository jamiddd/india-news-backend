from __future__ import annotations

import logging
import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import desc, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Article, DailyQuiz, DailySpellingBee, DailyWordLadder, StoryCluster
from app.services.llm_gen import call_claude_json

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

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
    ("SAME", "COST", ["SAME", "CAME", "CAMP", "COMP", "COOP", "COOT", "COST", "COME", "CAST", "CASE", "MOST"], 6),
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
    if not (8 <= len(words) <= 20) or len(set(words)) != len(words):
        raise ValueError("Need 8-20 unique words")
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
    system = (
        "You invent Spelling Bee puzzles (like the NYT game) for a general-audience daily "
        "puzzle app. Pick 7 unique letters and a list of 8-20 valid, common English words "
        "(4+ letters) that use only those 7 letters, each word may reuse letters, and every "
        "word must contain a chosen center letter. Include at least one pangram — a word "
        'using all 7 letters at least once. Return JSON only: {"letters": [7 single '
        'uppercase letters], "center_letter": "one of those letters", "words": [...]}'
    )
    data = await call_claude_json(system=system, user_content="Generate today's Spelling Bee puzzle.", max_tokens=800)
    if data is not None:
        try:
            letters, center, words = _validate_bee(data)
            return letters, center, words, "ai"
        except Exception as exc:
            logger.warning("Spelling Bee AI output failed validation: %s", exc)
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
    if not (6 <= len(allowed) <= 15) or len(set(allowed)) != len(allowed):
        raise ValueError("Need 6-15 unique allowed words")
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
    system = (
        "You invent Word Ladder puzzles for a general-audience daily puzzle app. Pick a "
        "start word and a target word, both common 4-letter English words, plus a pool of "
        "6-15 unique valid English words (same length as start/target) — the pool must "
        "contain a path from start to target where each step changes exactly one letter "
        'from a previous pool/start word. Return JSON only: {"start_word": "...", '
        '"target_word": "...", "allowed_words": [pool of words, not including start/target], '
        '"optimal_steps": integer length of the shortest such path}'
    )
    data = await call_claude_json(system=system, user_content="Generate today's Word Ladder puzzle.", max_tokens=500)
    if data is not None:
        try:
            start, target, allowed, optimal = _validate_ladder(data)
            return start, target, allowed, optimal, "ai"
        except Exception as exc:
            logger.warning("Word Ladder AI output failed validation: %s", exc)
    start, target, allowed, optimal = _fallback_ladder(puzzle_date)
    return start, target, allowed, optimal, "curated"


# ---------------------------------------------------------------------------
# Daily Quiz — generated from today's top corroborated stories
# ---------------------------------------------------------------------------

async def fetch_today_top_stories(session: AsyncSession, puzzle_date: date, limit: int = 8) -> list[dict]:
    """Same shape as polls.py's generate_draft: recent, multi-source-corroborated
    clusters ranked by headline_score, filtered for sensitive topics, reduced to a
    compact payload of headline/summary/outlet-snippets suitable for an LLM prompt."""
    day_start = datetime.combine(puzzle_date, time.min, tzinfo=IST)
    day_end = day_start + timedelta(days=1)
    candidates = (await session.execute(
        select(StoryCluster).options(selectinload(StoryCluster.articles).selectinload(Article.source))
        .where(StoryCluster.first_seen_at >= day_start, StoryCluster.first_seen_at < day_end, StoryCluster.distinct_source_count >= 2)
        .order_by(desc(StoryCluster.headline_score)).limit(20)
    )).scalars().all()
    safe = [cluster for cluster in candidates if not UNSAFE.search(cluster.headline or "")][:limit]
    return [{
        "cluster_id": cluster.id,
        "headline": cluster.headline,
        "summary": cluster.summary,
        "articles": [{"outlet": a.source.name if a.source else "Source", "headline": a.title, "snippet": (a.snippet or "")[:220]} for a in cluster.articles[:5]],
    } for cluster in safe]


def _validate_quiz(payload: dict) -> list[dict]:
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


async def generate_quiz(session: AsyncSession, puzzle_date: date) -> tuple[list[dict], str]:
    stories = await fetch_today_top_stories(session, puzzle_date)
    if len(stories) >= 3:
        system = (
            "You write a 5-question daily news quiz for a general-audience news app, based "
            "strictly on the supplied stories. Do not invent facts not present in the "
            "material. Avoid questions about death, violence, tragedy or crime even if "
            "mentioned in the source material — prefer factual, context, or figures-based "
            'questions. Return JSON only: {"questions": [{"question": "...", "options": '
            '[4 strings], "correct_index": 0-3, "explanation": "one sentence, cites the fact"}, '
            "... exactly 5 items]}"
        )
        data = await call_claude_json(system=system, user_content=str(stories), max_tokens=1500)
        if data is not None:
            try:
                return _validate_quiz(data), "ai"
            except Exception as exc:
                logger.warning("Quiz AI output failed validation: %s", exc)
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
        questions, source = await generate_quiz(session, puzzle_date)
        quiz = DailyQuiz(puzzle_date=puzzle_date, questions=questions, source=source)
        session.add(quiz)

    await session.commit()
    return bee, ladder, quiz
