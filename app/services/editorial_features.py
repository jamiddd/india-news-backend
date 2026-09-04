from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import DailyEditorial
from app.services.apiverve_client import call_apiverve
from app.services.editorial_backgrounds import pick_background, public_url
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


def _validate_word(word: dict) -> dict:
    word_fields = ("word", "pronunciation", "part_of_speech", "definition", "example", "origin")
    if any(not str(word.get(f) or "").strip() for f in word_fields):
        raise ValueError("Incomplete word fields")
    if not str(word["word"]).strip().replace(" ", "").isalpha():
        raise ValueError("Word must be alphabetic")
    word_out = {f: str(word[f]).strip() for f in word_fields}
    word_out["word"] = word_out["word"].upper()
    return word_out


def _validate_quote(quote: dict) -> dict:
    quote_fields = ("quote", "author")
    if any(not str(quote.get(f) or "").strip() for f in quote_fields):
        raise ValueError("Incomplete quote fields")
    return {f: str(quote[f]).strip() for f in quote_fields}


def _validate_word_and_quote(payload: dict) -> tuple[dict, dict]:
    return _validate_word(payload.get("word") or {}), _validate_quote(payload.get("quote") or {})


WORD_KEY_LENGTH = 6


def word_key(word: str) -> str:
    """Collision key for Word of the Day uniqueness.

    Deliberately coarser than the word itself: SERENDIPITY and SERENDIPITOUS
    are the same pick as far as a reader is concerned, and both shipped three
    days apart because the old avoid-list compared exact strings. A shared
    prefix catches that without pulling in a stemmer. It over-matches on
    occasion (two genuinely different words sharing six letters), which is
    why this drives the *soft* check only — a false collision costs one
    regeneration, whereas the DB index below matches on the whole word and so
    can never reject a legitimate pick.
    """
    return word.strip().upper()[:WORD_KEY_LENGTH]


async def _recent_words_and_authors(
    session: AsyncSession, feature_date: date, days: int = 21
) -> tuple[list[str], list[str], set[str]]:
    """Returns (recent words for the prompt, recent authors, every word key
    ever used).

    Two different windows on purpose. The prompt gets a bounded `days` slice
    because every word in it costs tokens and only steers a suggestion. The
    uniqueness check gets *all* history, because a 21-day window silently
    permits a true repeat the moment the feature is older than three weeks.
    Reading the lot is free: one row per day, and a full scan of the table
    measures 0.1 ms — nothing next to the four network calls the caller
    makes right after this.
    """
    start = feature_date - timedelta(days=days)
    result = await session.execute(
        select(DailyEditorial.feature_date, DailyEditorial.word, DailyEditorial.quote)
        .where(DailyEditorial.feature_date != feature_date)
    )
    words, authors, used_keys = [], [], set()
    for row_date, word, quote in result.all():
        if word and word.get("word"):
            used_keys.add(word_key(str(word["word"])))
            if row_date >= start:
                words.append(str(word["word"]))
        if quote and quote.get("author") and row_date >= start:
            authors.append(str(quote["author"]))
    return words, authors, used_keys


async def _apiverve_quote(recent_authors: list[str] | None = None) -> dict | None:
    """APIVerve's Random Quote has no topic/avoid-author filter, so draw a
    few and prefer one whose author wasn't used recently — same intent as
    the Claude prompt's avoid-list, just done client-side."""
    recent = {author.casefold() for author in (recent_authors or [])}
    first_valid: dict | None = None
    for attempt in range(5):
        if attempt > 0:
            await asyncio.sleep(0.5)
        data = await call_apiverve("randomquote")
        if data is None:
            continue
        try:
            quote = _validate_quote(data)
        except Exception as exc:
            logger.info("APIVerve random quote unusable: %s", exc)
            continue
        if first_valid is None:
            first_valid = quote
        if quote["author"].casefold() not in recent:
            return quote
    return first_valid


async def _ai_word(
    feature_date: date,
    recent_words: list[str] | None = None,
    used_keys: set[str] | None = None,
    attempts: int = 3,
) -> dict | None:
    """Ask Claude for a word, and actually enforce that it is a new one.

    The avoid-list used to be prompt text and nothing more, so a repeat was
    stored rather than retried — which is how SERENDIPITOUS and SERENDIPITY
    both shipped inside one week. Now a collision is a rejection: retry, and
    name the offending word in the follow-up so the next attempt differs
    instead of re-rolling the identical prompt.
    """
    used_keys = used_keys or set()
    avoid = f" Do not reuse any of these recent words: {', '.join(recent_words)}." if recent_words else ""
    system = (
        "You generate content for a daily 'Word of the Day' feature in a general-audience "
        "news app. Pick an interesting, moderately advanced English word (not obscure or "
        "offensive). Vary your picks meaningfully day to day — avoid always reaching for "
        f"the single most predictable/cliché answer (e.g. 'serendipity').{avoid} Return "
        'JSON only: {"word": "...", "pronunciation": "phonetic spelling", "part_of_speech": '
        '"noun/verb/adjective/etc", "definition": "...", "example": "a sentence using the '
        'word", "origin": "brief etymology"}'
    )
    user_content = f"Generate the word of the day for {feature_date.isoformat()}."
    for _ in range(attempts):
        data = await call_claude_json(
            system=system,
            user_content=user_content,
            max_tokens=500,
            temperature=1.0,
        )
        if data is None:
            return None
        try:
            word = _validate_word(data)
        except Exception as exc:
            logger.warning("Word-of-the-day AI output failed validation: %s", exc)
            return None
        if word_key(word["word"]) not in used_keys:
            return word
        logger.info("Word-of-the-day repeat rejected: %s", word["word"])
        user_content = (
            f"Generate the word of the day for {feature_date.isoformat()}. "
            f"{word['word']} has already been used — pick a different word, "
            "and not one sharing its opening letters."
        )
    logger.warning("Word-of-the-day: %s attempts all returned already-used words", attempts)
    return None


def _unused_curated_word(ordinal: int, used_keys: set[str]) -> dict:
    """Curated bank pick that isn't already in history — the rotation is
    ordinal % len(WORDS), so on a bank smaller than the feature's lifetime it
    comes back around and would otherwise re-serve a word verbatim. Walks
    forward from the usual slot and falls back to it if the whole bank is
    used up."""
    for offset in range(len(WORDS)):
        candidate = WORDS[(ordinal + offset) % len(WORDS)]
        if word_key(candidate["word"]) not in used_keys:
            return candidate
    return WORDS[ordinal % len(WORDS)]


async def generate_word_and_quote(
    feature_date: date,
    recent_words: list[str] | None = None,
    recent_authors: list[str] | None = None,
    used_keys: set[str] | None = None,
) -> tuple[dict, dict, str]:
    """Quote of the Day comes from APIVerve first (falling back to the
    combined Claude prompt, then the curated bank, for the quote only).
    Word of the Day still goes through Claude then the curated bank — see
    the "check APIVerve for Services" investigation: APIVerve's Dictionary/
    Random Word APIs can't supply pronunciation/part_of_speech/example/
    origin, so they're not a fit for this schema. (Re-probed 2026-09-03:
    APIVerve's randomwords endpoint does exist, but draws from a raw
    unabridged list — antiattrition, torulose, fellowless — with null
    definitions, so it can't pick the word either.)

    `used_keys` carries every word key already published; every branch below
    has to respect it, or the uniqueness fix only covers the happy path."""
    ordinal = feature_date.toordinal()
    used_keys = used_keys or set()

    quote = await _apiverve_quote(recent_authors)
    quote_source = "apiverve" if quote is not None else None

    word = await _ai_word(feature_date, recent_words, used_keys)
    word_source = "ai" if word is not None else None

    if word is None or quote is None:
        # Either half missed — the combined prompt is still the richest
        # single fallback, and cheaper than two separate retries.
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
        combined = None
        if data is not None:
            try:
                combined = _validate_word_and_quote(data)
            except Exception as exc:
                logger.warning("Word/quote AI output failed validation: %s", exc)
        if word is None:
            if combined is not None and word_key(combined[0]["word"]) not in used_keys:
                word, word_source = combined[0], "ai"
            else:
                word, word_source = _unused_curated_word(ordinal, used_keys), "curated"
        if quote is None:
            if combined is not None:
                quote, quote_source = combined[1], "ai"
            else:
                fallback_quote, fallback_author = QUOTES[ordinal % len(QUOTES)]
                quote, quote_source = {"quote": fallback_quote, "author": fallback_author}, "curated"

    source = quote_source if quote_source == word_source else f"{word_source}+{quote_source}"
    return word, quote, source


async def _ensure_background(session: AsyncSession, row: DailyEditorial, feature_date: date) -> DailyEditorial:
    """Fill in (or re-point) the row's background image.

    Two cases need this on read rather than only at creation: rows written
    while the bucket was unreachable or unconfigured (background_image is
    NULL), and rows written by the old Unsplash implementation, whose stored
    url points at a CDN we no longer use. Both are cheap now — the bucket
    listing is Redis-cached, so this is a dict compare and, at most, one
    UPDATE the first time a given date is read after the switch."""
    current = row.background_image or {}
    current_url = str(current.get("url") or "")
    if current_url.startswith(public_url("")):
        return row
    background = await pick_background(feature_date)
    if background is None or background == row.background_image:
        return row
    row.background_image = background
    await session.commit()
    await session.refresh(row)
    return row


async def get_or_create_editorial(session: AsyncSession, feature_date: date) -> DailyEditorial:
    result = await session.execute(select(DailyEditorial).where(DailyEditorial.feature_date == feature_date))
    existing = result.scalar_one_or_none()
    if existing:
        return await _ensure_background(session, existing, feature_date)
    lock_key = 77000000 + int(feature_date.strftime("%Y%m%d"))
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
    result = await session.execute(select(DailyEditorial).where(DailyEditorial.feature_date == feature_date))
    existing = result.scalar_one_or_none()
    if existing:
        return await _ensure_background(session, existing, feature_date)
    recent_words, recent_authors, used_keys = await _recent_words_and_authors(session, feature_date)
    word, quote, _ = await generate_word_and_quote(feature_date, recent_words, recent_authors, used_keys)
    row = DailyEditorial(
        feature_date=feature_date,
        word=word,
        quote=quote,
        background_image=await pick_background(feature_date),
        historical_events=await _fetch_events(feature_date),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row
