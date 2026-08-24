import logging
from datetime import date, timedelta

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DailyEditorial
from app.services.llm_gen import call_claude_json

logger = logging.getLogger(__name__)

WORDS = [
    {"word":"SERENDIPITY","pronunciation":"seh-ruhn-DIP-uh-tee","part_of_speech":"noun","definition":"The fortunate discovery of something valuable or interesting by chance.","example":"Finding the quiet bookshop was a moment of pure serendipity.","origin":"Coined by Horace Walpole in 1754 from the tale The Three Princes of Serendip."},
    {"word":"RESILIENT","pronunciation":"rih-ZIL-yuhnt","part_of_speech":"adjective","definition":"Able to recover quickly from difficulty or change.","example":"The resilient community rebuilt after the storm.","origin":"From Latin resilire, meaning to spring back."},
    {"word":"ELOQUENT","pronunciation":"EL-uh-kwuhnt","part_of_speech":"adjective","definition":"Fluent, persuasive, and graceful in expression.","example":"Her eloquent argument changed the course of the debate.","origin":"From Latin eloqui, meaning to speak out."},
    {"word":"PRAGMATIC","pronunciation":"prag-MAT-ik","part_of_speech":"adjective","definition":"Dealing with problems in a practical rather than theoretical way.","example":"They adopted a pragmatic solution that could be implemented immediately.","origin":"From Greek pragmatikos, relating to action or affairs."},
    {"word":"LUMINOUS","pronunciation":"LOO-muh-nuhs","part_of_speech":"adjective","definition":"Giving off light, or appearing bright and clear.","example":"A luminous moon rose above the trees.","origin":"From Latin lumen, meaning light."},
    {"word":"TENACIOUS","pronunciation":"tuh-NAY-shuhs","part_of_speech":"adjective","definition":"Persistent and unwilling to give up.","example":"The tenacious researcher pursued the answer for years.","origin":"From Latin tenax, meaning holding fast."},
    {"word":"EQUANIMITY","pronunciation":"ee-kwuh-NIM-uh-tee","part_of_speech":"noun","definition":"Calmness and composure, especially in a difficult situation.","example":"She received the unexpected news with equanimity.","origin":"From Latin aequanimitas, meaning evenness of mind."},
    {"word":"MELLIFLUOUS","pronunciation":"meh-LIF-loo-uhs","part_of_speech":"adjective","definition":"Pleasantly smooth and musical to hear.","example":"The singer's mellifluous voice filled the hall.","origin":"From Latin mel, honey, and fluere, to flow."},
]

QUOTES = [
    ("The future depends on what you do today.", "Mahatma Gandhi"),
    ("Arise, awake, and stop not till the goal is reached.", "Swami Vivekananda"),
    ("You cannot cross the sea merely by standing and staring at the water.", "Rabindranath Tagore"),
    ("The important thing is not to stop questioning.", "Albert Einstein"),
    ("Success is not final, failure is not fatal: it is the courage to continue that counts.", "Winston Churchill"),
    ("It always seems impossible until it's done.", "Nelson Mandela"),
    ("The journey of a thousand miles begins with one step.", "Lao Tzu"),
    ("Knowledge is power.", "Francis Bacon"),
]


async def _fetch_events(day: date) -> list[dict]:
    url = f"https://en.wikipedia.org/api/rest_v1/feed/onthisday/events/{day.month:02d}/{day.day:02d}"
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "OpenIndianNews/1.0 (https://openindiannews.com)"}) as client:
        response = await client.get(url)
        response.raise_for_status()
        raw_events = response.json().get("events", [])
    events = []
    for item in raw_events[:10]:
        pages = item.get("pages") or []
        article_url = None
        if pages:
            article_url = (((pages[0].get("content_urls") or {}).get("desktop") or {}).get("page"))
        events.append({"year": int(item["year"]), "text": str(item["text"]), "article_url": article_url})
    if not events:
        raise RuntimeError("Wikimedia returned no historical events")
    return events


def _validate_word_and_quote(payload: dict) -> tuple[dict, dict]:
    word = payload.get("word") or {}
    quote = payload.get("quote") or {}
    word_fields = ("word", "pronunciation", "part_of_speech", "definition", "example", "origin")
    quote_fields = ("quote", "author")
    if any(not str(word.get(f) or "").strip() for f in word_fields):
        raise ValueError("Incomplete word fields")
    if any(not str(quote.get(f) or "").strip() for f in quote_fields):
        raise ValueError("Incomplete quote fields")
    if not str(word["word"]).strip().replace(" ", "").isalpha():
        raise ValueError("Word must be alphabetic")
    word_out = {f: str(word[f]).strip() for f in word_fields}
    word_out["word"] = word_out["word"].upper()
    quote_out = {f: str(quote[f]).strip() for f in quote_fields}
    return word_out, quote_out


async def _recent_words_and_authors(session: AsyncSession, feature_date: date, days: int = 21) -> tuple[list[str], list[str]]:
    """Look back `days` days (excluding feature_date itself) for words/authors
    already used, so the prompt can steer Claude away from repeating them —
    without this, a small/cheap model tends to collapse onto the same
    "obvious" answer (e.g. SERENDIPITY, a Steve Jobs quote) regardless of
    the date, since nothing else in the prompt varies the content."""
    start = feature_date - timedelta(days=days)
    result = await session.execute(
        select(DailyEditorial.word, DailyEditorial.quote)
        .where(DailyEditorial.feature_date >= start, DailyEditorial.feature_date < feature_date)
    )
    words, authors = [], []
    for word, quote in result.all():
        if word and word.get("word"):
            words.append(str(word["word"]))
        if quote and quote.get("author"):
            authors.append(str(quote["author"]))
    return words, authors


async def generate_word_and_quote(feature_date: date, recent_words: list[str] | None = None, recent_authors: list[str] | None = None) -> tuple[dict, dict, str]:
    """Ask Claude for a fresh word-of-the-day + quote-of-the-day pair. Falls
    back to the curated WORDS/QUOTES banks, deterministically picked by
    date, on failure — same resilience pattern as
    word_search.generate_theme_and_words."""
    avoid = ""
    if recent_words:
        avoid += f" Do not reuse any of these recent words: {', '.join(recent_words)}."
    if recent_authors:
        avoid += f" Prefer an author not in this recent list: {', '.join(recent_authors)}."
    system = (
        "You generate content for a daily 'Word of the Day' and 'Quote of the Day' "
        "feature in a general-audience news app. Pick an interesting, moderately "
        "advanced English word (not obscure or offensive) and a real, attributable "
        "inspirational or thought-provoking quote from a known historical or public "
        "figure. Vary your picks meaningfully day to day — avoid always reaching for "
        "the single most predictable/cliché answer (e.g. 'serendipity', a generic "
        f"Steve Jobs quote).{avoid} Return JSON only: "
        '{"word": {"word": "...", "pronunciation": "phonetic spelling", '
        '"part_of_speech": "noun/verb/adjective/etc", "definition": "...", '
        '"example": "a sentence using the word", "origin": "brief etymology"}, '
        '"quote": {"quote": "the quote text", "author": "who said it"}}'
    )
    data = await call_claude_json(
        system=system,
        user_content=f"Generate the word and quote of the day for {feature_date.isoformat()}.",
        max_tokens=800,
        temperature=1.0,
    )
    if data is not None:
        try:
            return (*_validate_word_and_quote(data), "ai")
        except Exception as exc:
            logger.warning("Word/quote AI output failed validation: %s", exc)
    ordinal = feature_date.toordinal()
    quote, author = QUOTES[ordinal % len(QUOTES)]
    return WORDS[ordinal % len(WORDS)], {"quote": quote, "author": author}, "curated"


async def get_or_create_editorial(session: AsyncSession, feature_date: date) -> DailyEditorial:
    result = await session.execute(select(DailyEditorial).where(DailyEditorial.feature_date == feature_date))
    existing = result.scalar_one_or_none()
    if existing:
        return existing
    lock_key = 77000000 + int(feature_date.strftime("%Y%m%d"))
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
    result = await session.execute(select(DailyEditorial).where(DailyEditorial.feature_date == feature_date))
    existing = result.scalar_one_or_none()
    if existing:
        return existing
    recent_words, recent_authors = await _recent_words_and_authors(session, feature_date)
    word, quote, _ = await generate_word_and_quote(feature_date, recent_words, recent_authors)
    row = DailyEditorial(
        feature_date=feature_date,
        word=word,
        quote=quote,
        historical_events=await _fetch_events(feature_date),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row
