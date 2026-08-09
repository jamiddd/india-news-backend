from datetime import date

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DailyEditorial

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
    ordinal = feature_date.toordinal()
    quote, author = QUOTES[ordinal % len(QUOTES)]
    row = DailyEditorial(
        feature_date=feature_date,
        word=WORDS[ordinal % len(WORDS)],
        quote={"quote": quote, "author": author},
        historical_events=await _fetch_events(feature_date),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row
