import asyncio
import os
import sys
from sqlalchemy.future import select

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from backend.app.database import AsyncSessionLocal, engine, Base
from backend.app.models import Source

VERIFIED_SOURCES = [
    {
        "name": "The Hindu",
        "slug": "the-hindu",
        "feed_url": "https://www.thehindu.com/news/national/feeder/default.rss",
        "homepage_url": "https://www.thehindu.com",
        "category": "national",
        "region": "national"
    },
    {
        "name": "Indian Express",
        "slug": "indian-express",
        "feed_url": "https://indianexpress.com/section/india/feed/",
        "homepage_url": "https://indianexpress.com",
        "category": "national",
        "region": "national"
    },
    {
        "name": "NDTV",
        "slug": "ndtv",
        "feed_url": "https://feeds.feedburner.com/ndtvnews-top-stories",
        "homepage_url": "https://www.ndtv.com",
        "category": "national",
        "region": "national"
    },
    {
        "name": "Hindustan Times",
        "slug": "hindustan-times",
        "feed_url": "https://www.hindustantimes.com/feeds/rss/india-news/rssfeed.xml",
        "homepage_url": "https://www.hindustantimes.com",
        "category": "national",
        "region": "national"
    },
    {
        "name": "Times of India (Top)",
        "slug": "toi-top",
        "feed_url": "https://timesofindia.indiatimes.com/rssfeedstopstories.cms",
        "homepage_url": "https://timesofindia.indiatimes.com",
        "category": "national",
        "region": "national"
    },
    {
        "name": "Times of India (India)",
        "slug": "toi-india",
        "feed_url": "https://timesofindia.indiatimes.com/rssfeeds/-2128936835.cms",
        "homepage_url": "https://timesofindia.indiatimes.com",
        "category": "national",
        "region": "national"
    },
    {
        "name": "News18",
        "slug": "news18",
        "feed_url": "https://www.news18.com/commonfeeds/v1/eng/rss/india.xml",
        "homepage_url": "https://www.news18.com",
        "category": "national",
        "region": "national"
    },
    {
        "name": "India Today",
        "slug": "india-today",
        "feed_url": "https://www.indiatoday.in/rss/1206578",
        "homepage_url": "https://www.indiatoday.in",
        "category": "national",
        "region": "national"
    },
    {
        "name": "Livemint",
        "slug": "livemint",
        "feed_url": "https://www.livemint.com/rss/news",
        "homepage_url": "https://www.livemint.com",
        "category": "business",
        "region": "national"
    },
    {
        "name": "Moneycontrol",
        "slug": "moneycontrol",
        "feed_url": "https://www.moneycontrol.com/rss/MCtopnews.xml",
        "homepage_url": "https://www.moneycontrol.com",
        "category": "business",
        "region": "national"
    },
    {
        "name": "Economic Times",
        "slug": "economic-times",
        "feed_url": "https://economictimes.indiatimes.com/rssfeedstopstories.cms",
        "homepage_url": "https://economictimes.indiatimes.com",
        "category": "business",
        "region": "national"
    },
    {
        "name": "Business Today",
        "slug": "business-today",
        "feed_url": "https://www.businesstoday.in/rssfeeds?id=home",
        "homepage_url": "https://www.businesstoday.in",
        "category": "business",
        "region": "national"
    },
    {
        "name": "PIB Press Releases",
        "slug": "pib",
        "feed_url": "https://www.pib.gov.in/RssMain.aspx?ModId=6&Lang=2&Regid=3&reg=48",
        "homepage_url": "https://www.pib.gov.in",
        "category": "official",
        "region": "national"
    },
    # Phase 5: Regional Depth & Northeast India
    {
        "name": "EastMojo (Northeast)",
        "slug": "eastmojo-ne",
        "feed_url": "https://www.eastmojo.com/feed/",
        "homepage_url": "https://www.eastmojo.com",
        "category": "northeast",
        "region": "northeast"
    },
    {
        "name": "Assam Tribune (Northeast)",
        "slug": "assam-tribune",
        "feed_url": "https://assamtribune.com/feed",
        "homepage_url": "https://assamtribune.com",
        "category": "northeast",
        "region": "northeast"
    },
    {
        "name": "Northeast Today",
        "slug": "northeast-today",
        "feed_url": "https://www.northeasttoday.in/feed/",
        "homepage_url": "https://www.northeasttoday.in",
        "category": "northeast",
        "region": "northeast"
    }
]

async def seed_sources():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        seeded = 0
        for data in VERIFIED_SOURCES:
            res = await session.execute(select(Source).where(Source.slug == data["slug"]))
            existing = res.scalar_one_or_none()
            if not existing:
                src = Source(**data)
                session.add(src)
                seeded += 1
        await session.commit()
        print(f"Successfully seeded {seeded} sources into database (Total configured: {len(VERIFIED_SOURCES)}).")

if __name__ == "__main__":
    asyncio.run(seed_sources())
