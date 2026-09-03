"""
Migration: enforce Word of the Day uniqueness in the database.

The Python-side check in editorial_features.py is what actually prevents
repeats — it runs before the four network calls get_or_create_editorial
makes, so a collision costs one cheap retry of the word call rather than a
redo of everything. This index is the backstop for the paths that never go
through that function at all: force_editorial_regen.py writes the row
directly, and so would any future fix-up script.

The index matches on the whole uppercased word, not the 6-character prefix
the soft check uses. A prefix is right for a soft check (a false collision
just triggers a regeneration) and wrong for a hard constraint, where it
would permanently reject a legitimate word that happened to share six
opening letters with an old one.

Existing duplicates block index creation, so this reports them and stops
rather than half-applying. As of 2026-09-03 there is one: SERENDIPITOUS
(2026-08-23) and SERENDIPITY (2026-08-26), which collide on the soft key but
NOT on the exact word — so they do not block this index, and are listed as a
warning only. Clear any exact duplicate with force_editorial_regen.py on the
offending date before re-running.

Usage (inside the app container):
    python3 scripts/add_editorial_word_unique_index.py --check   # report only
    python3 scripts/add_editorial_word_unique_index.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import text

from app.database import AsyncSessionLocal
from app.services.editorial_features import WORD_KEY_LENGTH

INDEX_NAME = "uq_editorial_word"
CREATE_SQL = f"""
CREATE UNIQUE INDEX IF NOT EXISTS {INDEX_NAME}
    ON daily_editorial_features (upper(word->>'word'))
"""


async def main(check_only: bool) -> int:
    async with AsyncSessionLocal() as session:
        exact = (await session.execute(text(
            "SELECT upper(word->>'word') AS w, count(*), array_agg(feature_date ORDER BY feature_date) "
            "FROM daily_editorial_features GROUP BY 1 HAVING count(*) > 1 ORDER BY 2 DESC"
        ))).all()
        soft = (await session.execute(text(
            "SELECT left(upper(word->>'word'), :n) AS k, count(*), "
            "array_agg(word->>'word' ORDER BY feature_date) "
            "FROM daily_editorial_features GROUP BY 1 HAVING count(*) > 1 ORDER BY 2 DESC"
        ), {"n": WORD_KEY_LENGTH})).all()

        for key, count, words in soft:
            print(f"soft-key collision {key}: {count} rows -> {list(words)}")
        for word, count, dates in exact:
            print(f"EXACT duplicate {word}: {count} rows -> {[str(d) for d in dates]}")
        if not soft and not exact:
            print("No duplicates.")

        if exact:
            print(f"\nRefusing: {len(exact)} exact duplicate word(s) would break the unique index.")
            print("Regenerate the later date of each pair with force_editorial_regen.py, then re-run.")
            return 1
        if check_only:
            print("\n--check: index not created.")
            return 0

        await session.execute(text(CREATE_SQL))
        await session.commit()
        exists = (await session.execute(text(
            "SELECT indexdef FROM pg_indexes WHERE indexname = :name"
        ), {"name": INDEX_NAME})).scalar_one_or_none()
        print(f"\nCreated: {exists}")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main("--check" in sys.argv)))
