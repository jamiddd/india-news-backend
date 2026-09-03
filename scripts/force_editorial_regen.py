"""
One-off: force today's (or a given date's) word-of-the-day + quote-of-the-day
to regenerate via Claude right now, overwriting whatever is currently stored
(including the old hardcoded WORDS/QUOTES rotation from before LLM
generation existed). Unlike get_or_create_editorial, this always
regenerates and overwrites — it doesn't return early on an existing row.
historical_events is left untouched (its Wikipedia fetch was never
hardcoded, so there's nothing to force there).

Usage (inside the app container):
    python3 scripts/force_editorial_regen.py [YYYY-MM-DD]
"""
import asyncio
import os
import sys
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import DailyEditorial
from app.services.editorial_features import (
    BACKGROUND_QUERIES,
    _fetch_background_image,
    _recent_words_and_authors,
    generate_word_and_quote,
)


async def main(target_date: date):
    async with AsyncSessionLocal() as session:
        recent_words, recent_authors, used_keys = await _recent_words_and_authors(session, target_date)
        word, quote, source = await generate_word_and_quote(target_date, recent_words, recent_authors, used_keys)
        print(f"source: {source}")
        print(f"word: {word['word']}")
        print(f"quote: {quote['quote']!r} — {quote['author']}")

        background_query = BACKGROUND_QUERIES[target_date.toordinal() % len(BACKGROUND_QUERIES)]
        background_image = await _fetch_background_image(background_query)
        print(f"background_image: {background_image}")

        result = await session.execute(select(DailyEditorial).where(DailyEditorial.feature_date == target_date))
        existing = result.scalar_one_or_none()
        if existing is not None:
            existing.word = word
            existing.quote = quote
            existing.background_image = background_image
            await session.commit()
            print(f"Updated existing row for {target_date}.")
        else:
            print(f"No existing row for {target_date} — run the app's normal /word-of-the-day endpoint to create one (it will now use the LLM path).")


if __name__ == "__main__":
    target_date = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()
    asyncio.run(main(target_date))
