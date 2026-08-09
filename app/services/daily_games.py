from datetime import date

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DailyQuiz, DailySpellingBee, DailyWordLadder

SPELLING_BEES = [
    (list("AELPRST"), "A", ["ALERT", "ALTER", "APPEAL", "APPLE", "LATER", "LASER", "PEAR", "PLATE", "PLEAT", "RATE", "REAL", "SEAL", "SPARE", "SPEAR", "STAPLE", "STAR", "START", "STEAL", "TART"]),
    (list("AEGINRT"), "E", ["EAGER", "EARN", "EARRING", "EATING", "ENGINE", "ENTER", "ENTIRE", "GREEN", "INNER", "INTER", "RANGE", "REGAIN", "RENT", "RETAIN", "TEAR", "TREE"]),
    (list("ACILMOT"), "O", ["ATOM", "COAL", "COAT", "COIL", "COLA", "COMIC", "COOL", "LOOM", "LOOT", "MOAT", "TACO", "TOOL", "TOTAL"]),
    (list("DEINOST"), "I", ["DIET", "DINE", "DINO", "EDIT", "INSIDE", "INTEND", "NOISE", "ONION", "SITE", "TIDE", "TINE"]),
    (list("ABCEKLR"), "R", ["BAKER", "BARK", "BEAR", "BRAKE", "BREAK", "CARE", "CLEAR", "CRACK", "CREAK", "RACE", "RACK", "RARE", "REAL", "REBEL", "REEK"]),
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


async def get_or_create_daily_games(session: AsyncSession, puzzle_date: date):
    lock_key = 76000000 + int(puzzle_date.strftime("%Y%m%d"))
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
    ordinal = puzzle_date.toordinal()

    bee = (await session.execute(select(DailySpellingBee).where(DailySpellingBee.puzzle_date == puzzle_date))).scalar_one_or_none()
    if bee is None:
        letters, center, words = SPELLING_BEES[ordinal % len(SPELLING_BEES)]
        bee = DailySpellingBee(puzzle_date=puzzle_date, letters=letters, center_letter=center, words=words)
        session.add(bee)

    ladder = (await session.execute(select(DailyWordLadder).where(DailyWordLadder.puzzle_date == puzzle_date))).scalar_one_or_none()
    if ladder is None:
        start, target, allowed, optimal = WORD_LADDERS[ordinal % len(WORD_LADDERS)]
        ladder = DailyWordLadder(puzzle_date=puzzle_date, start_word=start, target_word=target, allowed_words=allowed, optimal_steps=optimal)
        session.add(ladder)

    quiz = (await session.execute(select(DailyQuiz).where(DailyQuiz.puzzle_date == puzzle_date))).scalar_one_or_none()
    if quiz is None:
        questions = [
            {"id": index + 1, "question": question, "options": options, "correct_index": correct, "explanation": explanation}
            for index, (question, options, correct, explanation) in enumerate(QUIZ_SETS[ordinal % len(QUIZ_SETS)])
        ]
        quiz = DailyQuiz(puzzle_date=puzzle_date, questions=questions)
        session.add(quiz)

    await session.commit()
    return bee, ladder, quiz
