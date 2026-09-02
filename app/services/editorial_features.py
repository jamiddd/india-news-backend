import logging
from datetime import date, timedelta

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import DailyEditorial
from app.services.apiverve_client import call_apiverve
from app.services.llm_gen import call_claude_json

logger = logging.getLogger(__name__)

# Rotated by date ordinal so the Quote of the Day background varies day to
# day without needing to parse the quote text for a topic. Kept deliberately
# mood/abstract rather than literal — it's a backdrop behind text, not
# illustration of the quote's content.
BACKGROUND_QUERIES = [
    "moody nature landscape",
    "dark abstract texture",
    "night sky stars",
    "misty mountains",
    "ocean waves dark",
    "minimalist shadow",
    "forest silhouette",
    "city lights night",
]

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


async def _fetch_background_image(query: str) -> dict | None:
    """Best-effort — returns None (no key, request failure, no results) and
    the app falls back to a plain gradient background. Attribution links
    carry Unsplash's required utm params; on-screen photographer credit is
    the app's responsibility (Unsplash API guidelines), not this function's."""
    if not settings.UNSPLASH_ACCESS_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                "https://api.unsplash.com/photos/random",
                params={"query": query, "orientation": "portrait", "content_filter": "high"},
                headers={"Authorization": f"Client-ID {settings.UNSPLASH_ACCESS_KEY}", "Accept-Version": "v1"},
            )
            response.raise_for_status()
            data = response.json()
        return {
            "url": data["urls"]["regular"],
            "photographer": data["user"]["name"],
            "photographer_url": f"{data['user']['links']['html']}?utm_source=openindiannews&utm_medium=referral",
            "unsplash_url": f"{data['links']['html']}?utm_source=openindiannews&utm_medium=referral",
        }
    except Exception as exc:
        logger.warning("Unsplash background fetch failed: %s", exc)
        return None


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


async def _apiverve_quote(recent_authors: list[str] | None = None) -> dict | None:
    """APIVerve's Random Quote has no topic/avoid-author filter, so draw a
    few and prefer one whose author wasn't used recently — same intent as
    the Claude prompt's avoid-list, just done client-side."""
    recent = {author.casefold() for author in (recent_authors or [])}
    first_valid: dict | None = None
    for _ in range(5):
        data = await call_apiverve("randomquote")
        if data is None:
            return first_valid
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


async def _ai_word(feature_date: date, recent_words: list[str] | None = None) -> dict | None:
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
    data = await call_claude_json(
        system=system,
        user_content=f"Generate the word of the day for {feature_date.isoformat()}.",
        max_tokens=500,
        temperature=1.0,
    )
    if data is None:
        return None
    try:
        return _validate_word(data)
    except Exception as exc:
        logger.warning("Word-of-the-day AI output failed validation: %s", exc)
        return None


async def generate_word_and_quote(feature_date: date, recent_words: list[str] | None = None, recent_authors: list[str] | None = None) -> tuple[dict, dict, str]:
    """Quote of the Day comes from APIVerve first (falling back to the
    combined Claude prompt, then the curated bank, for the quote only).
    Word of the Day still goes through Claude then the curated bank — see
    the "check APIVerve for Services" investigation: APIVerve's Dictionary/
    Random Word APIs can't supply pronunciation/part_of_speech/example/
    origin, so they're not a fit for this schema."""
    ordinal = feature_date.toordinal()

    quote = await _apiverve_quote(recent_authors)
    quote_source = "apiverve" if quote is not None else None

    word = await _ai_word(feature_date, recent_words)
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
            word = combined[0] if combined is not None else WORDS[ordinal % len(WORDS)]
            word_source = "ai" if combined is not None else "curated"
        if quote is None:
            if combined is not None:
                quote, quote_source = combined[1], "ai"
            else:
                fallback_quote, fallback_author = QUOTES[ordinal % len(QUOTES)]
                quote, quote_source = {"quote": fallback_quote, "author": fallback_author}, "curated"

    source = quote_source if quote_source == word_source else f"{word_source}+{quote_source}"
    return word, quote, source


async def _retry_missing_background(session: AsyncSession, row: DailyEditorial, feature_date: date) -> DailyEditorial:
    """The initial fetch is best-effort and its result gets cached on the
    row forever, so a transient failure (rate limit, missing key at the
    time, network blip) previously meant no background image for that date
    ever again. Retry once per request instead — cheap, since a populated
    background_image short-circuits immediately."""
    if row.background_image is not None:
        return row
    background_query = BACKGROUND_QUERIES[feature_date.toordinal() % len(BACKGROUND_QUERIES)]
    background_image = await _fetch_background_image(background_query)
    if background_image is not None:
        row.background_image = background_image
        await session.commit()
        await session.refresh(row)
    return row


async def get_or_create_editorial(session: AsyncSession, feature_date: date) -> DailyEditorial:
    result = await session.execute(select(DailyEditorial).where(DailyEditorial.feature_date == feature_date))
    existing = result.scalar_one_or_none()
    if existing:
        return await _retry_missing_background(session, existing, feature_date)
    lock_key = 77000000 + int(feature_date.strftime("%Y%m%d"))
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
    result = await session.execute(select(DailyEditorial).where(DailyEditorial.feature_date == feature_date))
    existing = result.scalar_one_or_none()
    if existing:
        return await _retry_missing_background(session, existing, feature_date)
    recent_words, recent_authors = await _recent_words_and_authors(session, feature_date)
    word, quote, _ = await generate_word_and_quote(feature_date, recent_words, recent_authors)
    background_query = BACKGROUND_QUERIES[feature_date.toordinal() % len(BACKGROUND_QUERIES)]
    row = DailyEditorial(
        feature_date=feature_date,
        word=word,
        quote=quote,
        background_image=await _fetch_background_image(background_query),
        historical_events=await _fetch_events(feature_date),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row
