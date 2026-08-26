import asyncio
import os
import sys
from sqlalchemy.future import select

# Ensure root of repo/backend is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import AsyncSessionLocal, engine, Base
from app.models import Source

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
        # Video-only feed, added to exercise the video-scraping pipeline
        # (see app/services/extractor.py's JW Player resolution) in
        # production — The Hindu's video pages embed JW Player, whose media
        # id resolves through JW's delivery API to a real .m3u8/.mp4.
        "name": "The Hindu Videos",
        "slug": "the-hindu-videos",
        "feed_url": "https://www.thehindu.com/videos/feeder/default.rss",
        "homepage_url": "https://www.thehindu.com/videos",
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
    },
    {
        "name": "Indian Express Sports",
        "slug": "indian-express-sports",
        "feed_url": "https://indianexpress.com/section/sports/feed/",
        "homepage_url": "https://indianexpress.com/section/sports/",
        "category": "sports",
        "region": "national"
    },
    {
        "name": "Hindustan Times Cricket",
        "slug": "hindustan-times-cricket",
        "feed_url": "https://www.hindustantimes.com/feeds/rss/cricket/rssfeed.xml",
        "homepage_url": "https://www.hindustantimes.com/cricket",
        "category": "sports",
        "region": "national"
    },
    {
        "name": "Times of India Sports",
        "slug": "toi-sports",
        "feed_url": "https://timesofindia.indiatimes.com/rssfeeds/4719148.cms",
        "homepage_url": "https://timesofindia.indiatimes.com/sports",
        "category": "sports",
        "region": "national"
    },
    {
        "name": "NDTV Sports",
        "slug": "ndtv-sports",
        "feed_url": "https://feeds.feedburner.com/ndtvsports-latest",
        "homepage_url": "https://sports.ndtv.com",
        "category": "sports",
        "region": "national"
    },
    {
        "name": "Indian Express Entertainment",
        "slug": "indian-express-entertainment",
        "feed_url": "https://indianexpress.com/section/entertainment/feed/",
        "homepage_url": "https://indianexpress.com/section/entertainment/",
        "category": "entertainment",
        "region": "national"
    },
    {
        "name": "Hindustan Times Entertainment",
        "slug": "hindustan-times-entertainment",
        "feed_url": "https://www.hindustantimes.com/feeds/rss/entertainment/rssfeed.xml",
        "homepage_url": "https://www.hindustantimes.com/entertainment",
        "category": "entertainment",
        "region": "national"
    },
    {
        "name": "Times of India Entertainment",
        "slug": "toi-entertainment",
        "feed_url": "https://timesofindia.indiatimes.com/rssfeeds/1081479906.cms",
        "homepage_url": "https://timesofindia.indiatimes.com/entertainment",
        "category": "entertainment",
        "region": "national"
    },
    {
        "name": "NDTV Movies",
        "slug": "ndtv-movies",
        "feed_url": "https://feeds.feedburner.com/ndtvmovies-latest",
        "homepage_url": "https://movies.ndtv.com",
        "category": "entertainment",
        "region": "national"
    },
    {
        "name": "Indian Express Technology",
        "slug": "indian-express-tech",
        "feed_url": "https://indianexpress.com/section/technology/feed/",
        "homepage_url": "https://indianexpress.com/section/technology/",
        "category": "tech",
        "region": "national"
    },
    {
        "name": "Hindustan Times Technology",
        "slug": "hindustan-times-tech",
        "feed_url": "https://www.hindustantimes.com/feeds/rss/technology/rssfeed.xml",
        "homepage_url": "https://www.hindustantimes.com/technology",
        "category": "tech",
        "region": "national"
    },
    {
        "name": "Gadgets 360 (NDTV)",
        "slug": "gadgets360",
        "feed_url": "https://feeds.feedburner.com/gadgets360-latest",
        "homepage_url": "https://www.gadgets360.com",
        "category": "tech",
        "region": "national"
    },
    {
        "name": "Times of India Technology",
        "slug": "toi-tech",
        "feed_url": "https://timesofindia.indiatimes.com/rssfeeds/66949542.cms",
        "homepage_url": "https://timesofindia.indiatimes.com/technology",
        "category": "tech",
        "region": "national"
    },
    {
        "name": "Indian Express Political Pulse",
        "slug": "indian-express-politics",
        "feed_url": "https://indianexpress.com/section/political-pulse/feed/",
        "homepage_url": "https://indianexpress.com/section/political-pulse/",
        "category": "politics",
        "region": "national"
    },
    {
        "name": "News18 Politics",
        "slug": "news18-politics",
        "feed_url": "https://www.news18.com/commonfeeds/v1/eng/rss/politics.xml",
        "homepage_url": "https://www.news18.com/politics/",
        "category": "politics",
        "region": "national"
    },
    {
        "name": "Livemint Politics",
        "slug": "livemint-politics",
        "feed_url": "https://www.livemint.com/rss/politics",
        "homepage_url": "https://www.livemint.com/politics",
        "category": "politics",
        "region": "national"
    },

    # --- Added 2026-08-09: national source/category expansion pass. Every
    # feed below was verified live (HTTP 200 + real XML + non-zero <item>
    # count) before being added — see india-news-app-handoff.md for the
    # full methodology and the honest coverage gaps (Gujarat, Rajasthan,
    # Goa, Bihar, Jharkhand, Madhya Pradesh, Chhattisgarh — no viable
    # publisher-sanctioned English RSS found for any of these).

    # South India (Tamil Nadu, Kerala, Karnataka, Andhra Pradesh, Telangana)
    {
        "name": "The Hindu (Tamil Nadu)",
        "slug": "the-hindu-tamil-nadu",
        "feed_url": "https://www.thehindu.com/news/national/tamil-nadu/feeder/default.rss",
        "homepage_url": "https://www.thehindu.com/news/national/tamil-nadu/",
        "category": "regional_south",
        "region": "south"
    },
    {
        "name": "The Hindu (Kerala)",
        "slug": "the-hindu-kerala",
        "feed_url": "https://www.thehindu.com/news/national/kerala/feeder/default.rss",
        "homepage_url": "https://www.thehindu.com/news/national/kerala/",
        "category": "regional_south",
        "region": "south"
    },
    {
        "name": "The Hindu (Karnataka)",
        "slug": "the-hindu-karnataka",
        "feed_url": "https://www.thehindu.com/news/national/karnataka/feeder/default.rss",
        "homepage_url": "https://www.thehindu.com/news/national/karnataka/",
        "category": "regional_south",
        "region": "south"
    },
    {
        "name": "The Hindu (Andhra Pradesh)",
        "slug": "the-hindu-andhra-pradesh",
        "feed_url": "https://www.thehindu.com/news/national/andhra-pradesh/feeder/default.rss",
        "homepage_url": "https://www.thehindu.com/news/national/andhra-pradesh/",
        "category": "regional_south",
        "region": "south"
    },
    {
        "name": "The Hindu (Telangana)",
        "slug": "the-hindu-telangana",
        "feed_url": "https://www.thehindu.com/news/national/telangana/feeder/default.rss",
        "homepage_url": "https://www.thehindu.com/news/national/telangana/",
        "category": "regional_south",
        "region": "south"
    },
    {
        "name": "New Indian Express (Tamil Nadu)",
        "slug": "nie-tamil-nadu",
        "feed_url": "https://www.newindianexpress.com/states/tamil-nadu/rssfeed/?id=170&getXmlFeed=true",
        "homepage_url": "https://www.newindianexpress.com/states/tamil-nadu/",
        "category": "regional_south",
        "region": "south"
    },
    {
        "name": "New Indian Express (Kerala)",
        "slug": "nie-kerala",
        "feed_url": "https://www.newindianexpress.com/states/kerala/rssfeed/?id=170&getXmlFeed=true",
        "homepage_url": "https://www.newindianexpress.com/states/kerala/",
        "category": "regional_south",
        "region": "south"
    },
    {
        "name": "New Indian Express (Karnataka)",
        "slug": "nie-karnataka",
        "feed_url": "https://www.newindianexpress.com/states/karnataka/rssfeed/?id=170&getXmlFeed=true",
        "homepage_url": "https://www.newindianexpress.com/states/karnataka/",
        "category": "regional_south",
        "region": "south"
    },
    {
        "name": "New Indian Express (Andhra Pradesh)",
        "slug": "nie-andhra-pradesh",
        "feed_url": "https://www.newindianexpress.com/states/andhra-pradesh/rssfeed/?id=170&getXmlFeed=true",
        "homepage_url": "https://www.newindianexpress.com/states/andhra-pradesh/",
        "category": "regional_south",
        "region": "south"
    },
    {
        "name": "New Indian Express (Telangana)",
        "slug": "nie-telangana",
        "feed_url": "https://www.newindianexpress.com/states/telangana/rssfeed/?id=170&getXmlFeed=true",
        "homepage_url": "https://www.newindianexpress.com/states/telangana/",
        "category": "regional_south",
        "region": "south"
    },
    {
        "name": "Deccan Chronicle",
        "slug": "deccan-chronicle",
        "feed_url": "https://www.deccanchronicle.com/feeds.xml",
        "homepage_url": "https://www.deccanchronicle.com",
        "category": "regional_south",
        "region": "south"
    },

    # West India (Maharashtra)
    {
        "name": "Free Press Journal",
        "slug": "free-press-journal",
        "feed_url": "https://www.freepressjournal.in/stories.rss",
        "homepage_url": "https://www.freepressjournal.in",
        "category": "regional_west",
        "region": "west"
    },
    {
        "name": "Mid-Day (Mumbai)",
        "slug": "mid-day-mumbai",
        "feed_url": "https://www.mid-day.com/Resources/midday/rss/mumbai-news.xml",
        "homepage_url": "https://www.mid-day.com",
        "category": "regional_west",
        "region": "west"
    },

    # World
    {
        "name": "Indian Express World",
        "slug": "indian-express-world",
        "feed_url": "https://indianexpress.com/section/world/feed/",
        "homepage_url": "https://indianexpress.com/section/world/",
        "category": "world",
        "region": "national"
    },
    {
        "name": "Hindustan Times World",
        "slug": "hindustan-times-world",
        "feed_url": "https://www.hindustantimes.com/feeds/rss/world-news/rssfeed.xml",
        "homepage_url": "https://www.hindustantimes.com/world-news",
        "category": "world",
        "region": "national"
    },
    {
        "name": "Times of India World",
        "slug": "toi-world",
        "feed_url": "https://timesofindia.indiatimes.com/rssfeeds/296589292.cms",
        "homepage_url": "https://timesofindia.indiatimes.com/world",
        "category": "world",
        "region": "national"
    },
    {
        "name": "News18 World",
        "slug": "news18-world",
        "feed_url": "https://www.news18.com/commonfeeds/v1/eng/rss/world.xml",
        "homepage_url": "https://www.news18.com/world/",
        "category": "world",
        "region": "national"
    },
    {
        "name": "BBC World",
        "slug": "bbc-world",
        "feed_url": "https://feeds.bbci.co.uk/news/world/rss.xml",
        "homepage_url": "https://www.bbc.com/news/world",
        "category": "world",
        "region": "national"
    },

    # Health
    {
        "name": "Indian Express Health",
        "slug": "indian-express-health",
        "feed_url": "https://indianexpress.com/section/lifestyle/health/feed/",
        "homepage_url": "https://indianexpress.com/section/lifestyle/health/",
        "category": "health",
        "region": "national"
    },
    {
        "name": "Hindustan Times Health",
        "slug": "hindustan-times-health",
        "feed_url": "https://www.hindustantimes.com/feeds/rss/lifestyle/health/rssfeed.xml",
        "homepage_url": "https://www.hindustantimes.com/lifestyle/health",
        "category": "health",
        "region": "national"
    },
    {
        "name": "Times of India Health & Fitness",
        "slug": "toi-health",
        "feed_url": "https://timesofindia.indiatimes.com/rssfeeds/3908999.cms",
        "homepage_url": "https://timesofindia.indiatimes.com/life-style/health-fitness",
        "category": "health",
        "region": "national"
    },
    {
        # Confirmed working but only 1 item at verification time (2026-08-09)
        # — real, live, publisher-sanctioned feed, just thin. Kept in, not
        # excluded, but worth monitoring.
        "name": "News18 Health",
        "slug": "news18-health",
        "feed_url": "https://www.news18.com/commonfeeds/v1/eng/rss/health.xml",
        "homepage_url": "https://www.news18.com/health-and-fitness/",
        "category": "health",
        "region": "national"
    },

    # Science
    {
        "name": "Indian Express Science",
        "slug": "indian-express-science",
        "feed_url": "https://indianexpress.com/section/technology/science/feed/",
        "homepage_url": "https://indianexpress.com/section/technology/science/",
        "category": "science",
        "region": "national"
    },
    {
        "name": "Times of India Science",
        "slug": "toi-science",
        "feed_url": "https://timesofindia.indiatimes.com/rssfeeds/-2128672765.cms",
        "homepage_url": "https://timesofindia.indiatimes.com/science",
        "category": "science",
        "region": "national"
    },
    # Note: HT's technology/science feed (hindustantimes.com/feeds/rss/technology/science/rssfeed.xml)
    # returns HTTP 200 but is a broken stub (title: null, 0 items) — a
    # status-code false positive, deliberately not seeded. News18 has no
    # science section feed (returns a JSON 404, not XML). Science is the
    # thinnest topical category as a result — worth a future gap-fill
    # search (e.g. a reputable international science outlet) if parity
    # with the other categories matters.

    # Education
    {
        "name": "Indian Express Education",
        "slug": "indian-express-education",
        "feed_url": "https://indianexpress.com/section/education/feed/",
        "homepage_url": "https://indianexpress.com/section/education/",
        "category": "education",
        "region": "national"
    },
    {
        "name": "Times of India Education",
        "slug": "toi-education",
        "feed_url": "https://timesofindia.indiatimes.com/rssfeeds/913168846.cms",
        "homepage_url": "https://timesofindia.indiatimes.com/education",
        "category": "education",
        "region": "national"
    },
    {
        "name": "News18 Education and Career",
        "slug": "news18-education",
        "feed_url": "https://www.news18.com/commonfeeds/v1/eng/rss/education-career.xml",
        "homepage_url": "https://www.news18.com/education-career/",
        "category": "education",
        "region": "national"
    },
    {
        # Confirmed working but only 6 items at verification time — real and
        # correctly populated, just a thinner feed than the other three.
        "name": "Hindustan Times Education",
        "slug": "hindustan-times-education",
        "feed_url": "https://www.hindustantimes.com/feeds/rss/education/rssfeed.xml",
        "homepage_url": "https://www.hindustantimes.com/education",
        "category": "education",
        "region": "national"
    },

    # Crypto
    {
        "name": "News18 Cryptocurrency",
        "slug": "news18-crypto",
        "feed_url": "https://www.news18.com/commonfeeds/v1/eng/rss/cryptocurrency.xml",
        "homepage_url": "https://www.news18.com/cryptocurrency/",
        "category": "crypto",
        "region": "national"
    },
    {
        "name": "CoinDesk",
        "slug": "coindesk",
        "feed_url": "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "homepage_url": "https://www.coindesk.com",
        "category": "crypto",
        "region": "national"
    },
    {
        "name": "Cointelegraph",
        "slug": "cointelegraph",
        "feed_url": "https://cointelegraph.com/rss",
        "homepage_url": "https://cointelegraph.com",
        "category": "crypto",
        "region": "national"
    },
    # Note: Moneycontrol and Business Standard crypto feeds both return
    # HTTP 403 (WAF block) — same vendor pattern as Business Standard's
    # already-documented blanket block. No working TOI/Livemint crypto RSS
    # could be confirmed either (a guessed plausible TOI feed ID silently
    # returned an unrelated city feed instead of erroring — not worth the
    # risk of guessing another one).

    # Lifestyle
    {
        "name": "Indian Express Lifestyle",
        "slug": "indian-express-lifestyle",
        "feed_url": "https://indianexpress.com/section/lifestyle/feed/",
        "homepage_url": "https://indianexpress.com/section/lifestyle/",
        "category": "lifestyle",
        "region": "national"
    },
    {
        "name": "Hindustan Times Lifestyle",
        "slug": "hindustan-times-lifestyle",
        "feed_url": "https://www.hindustantimes.com/feeds/rss/lifestyle/rssfeed.xml",
        "homepage_url": "https://www.hindustantimes.com/lifestyle",
        "category": "lifestyle",
        "region": "national"
    },
    {
        "name": "Times of India Lifestyle",
        "slug": "toi-lifestyle",
        "feed_url": "https://timesofindia.indiatimes.com/rssfeeds/2886704.cms",
        "homepage_url": "https://timesofindia.indiatimes.com/life-style",
        "category": "lifestyle",
        "region": "national"
    },

    # --- Added 2026-08-25: expansion pass for national, business,
    # entertainment, tech, world. Every feed below was verified live
    # (HTTP 200 + real XML + non-zero <item> count + fresh pubDate on
    # 2026-08-25) before being added. Rejected during this pass: The Quint
    # (302/near-empty), Firstpost, Scroll.in, Deccan Herald, Outlook,
    # Financial Express, Business Standard, Bollywood Hungama, Filmibeat,
    # 91mobiles (all 403/404 — WAF-blocked or dead), Zee News business/tech/
    # entertainment (200 but 0 items), Analytics India Mag (200 but 0
    # items), CNN World and Reuters World (blocked/unreachable). India
    # Today's Nation/Economy section feeds are stale magazine-era content
    # (Economy last updated March 2025) — skipped; only India Today World
    # was fresh.

    {
        "name": "DNA India",
        "slug": "dna-india",
        "feed_url": "https://www.dnaindia.com/feeds/india.xml",
        "homepage_url": "https://www.dnaindia.com/india",
        "category": "national",
        "region": "national"
    },
    {
        "name": "CNBC-TV18 India",
        "slug": "cnbctv18-india",
        "feed_url": "https://www.cnbctv18.com/commonfeeds/v1/cne/rss/india.xml",
        "homepage_url": "https://www.cnbctv18.com/india/",
        "category": "national",
        "region": "national"
    },

    {
        "name": "CNBC-TV18 Business",
        "slug": "cnbctv18-business",
        "feed_url": "https://www.cnbctv18.com/commonfeeds/v1/cne/rss/business.xml",
        "homepage_url": "https://www.cnbctv18.com/business/",
        "category": "business",
        "region": "national"
    },
    {
        "name": "DNA Business",
        "slug": "dna-business",
        "feed_url": "https://www.dnaindia.com/feeds/business.xml",
        "homepage_url": "https://www.dnaindia.com/business",
        "category": "business",
        "region": "national"
    },

    {
        "name": "Pinkvilla",
        "slug": "pinkvilla",
        "feed_url": "https://www.pinkvilla.com/rss.xml",
        "homepage_url": "https://www.pinkvilla.com",
        "category": "entertainment",
        "region": "national"
    },
    {
        "name": "DNA Entertainment",
        "slug": "dna-entertainment",
        "feed_url": "https://www.dnaindia.com/feeds/entertainment.xml",
        "homepage_url": "https://www.dnaindia.com/entertainment",
        "category": "entertainment",
        "region": "national"
    },

    {
        "name": "MediaNama",
        "slug": "medianama",
        "feed_url": "https://www.medianama.com/feed/",
        "homepage_url": "https://www.medianama.com",
        "category": "tech",
        "region": "national"
    },
    {
        "name": "CNBC-TV18 Technology",
        "slug": "cnbctv18-tech",
        "feed_url": "https://www.cnbctv18.com/commonfeeds/v1/cne/rss/technology.xml",
        "homepage_url": "https://www.cnbctv18.com/technology/",
        "category": "tech",
        "region": "national"
    },
    {
        "name": "DNA Technology",
        "slug": "dna-tech",
        "feed_url": "https://www.dnaindia.com/feeds/technology.xml",
        "homepage_url": "https://www.dnaindia.com/technology",
        "category": "tech",
        "region": "national"
    },
    {
        "name": "TechRadar",
        "slug": "techradar",
        "feed_url": "https://www.techradar.com/rss",
        "homepage_url": "https://www.techradar.com",
        "category": "tech",
        "region": "national"
    },

    {
        "name": "India Today World",
        "slug": "india-today-world",
        "feed_url": "https://www.indiatoday.in/rss/1206577",
        "homepage_url": "https://www.indiatoday.in/world",
        "category": "world",
        "region": "national"
    },
    {
        "name": "CNBC-TV18 World",
        "slug": "cnbctv18-world",
        "feed_url": "https://www.cnbctv18.com/commonfeeds/v1/cne/rss/world.xml",
        "homepage_url": "https://www.cnbctv18.com/world/",
        "category": "world",
        "region": "national"
    },
    {
        "name": "DNA World",
        "slug": "dna-world",
        "feed_url": "https://www.dnaindia.com/feeds/world.xml",
        "homepage_url": "https://www.dnaindia.com/world",
        "category": "world",
        "region": "national"
    },
    {
        "name": "Al Jazeera",
        "slug": "al-jazeera",
        "feed_url": "https://www.aljazeera.com/xml/rss/all.xml",
        "homepage_url": "https://www.aljazeera.com",
        "category": "world",
        "region": "national"
    },
    {
        "name": "The Guardian World",
        "slug": "guardian-world",
        "feed_url": "https://www.theguardian.com/world/rss",
        "homepage_url": "https://www.theguardian.com/world",
        "category": "world",
        "region": "national"
    },
    {
        "name": "WSJ World",
        "slug": "wsj-world",
        "feed_url": "https://feeds.a.dj.com/rss/RSSWorldNews.xml",
        "homepage_url": "https://www.wsj.com/news/world",
        "category": "world",
        "region": "national"
    },
    {
        "name": "Sky News World",
        "slug": "sky-news-world",
        "feed_url": "https://feeds.skynews.com/feeds/rss/world.xml",
        "homepage_url": "https://news.sky.com/world",
        "category": "world",
        "region": "national"
    },
    {
        "name": "NPR World",
        "slug": "npr-world",
        "feed_url": "https://feeds.npr.org/1004/rss.xml",
        "homepage_url": "https://www.npr.org/sections/world/",
        "category": "world",
        "region": "national"
    },

    # --- Added 2026-08-25: state-gap fill pass. Prior handoff doc flagged
    # Gujarat, Rajasthan, Goa, Bihar, Jharkhand, Madhya Pradesh, and
    # Chhattisgarh as having no viable publisher-sanctioned English RSS.
    # Re-checked using TOI's own /rss.cms directory (rather than guessing
    # city feed IDs, which is what failed the first two times) and found
    # real, live, fresh feeds for 5 of the 7. Madhya Pradesh and
    # Chhattisgarh remain uncovered: tried Patrika, Jagran, Naidunia (all
    # 404), Free Press Journal Bhopal/Indore (200 but 0 items), and Dainik
    # Bhaskar's guessed Bhopal category feed (200 but content was generic
    # national news mislabeled under a Bhopal-looking category ID, not
    # real local coverage) — no working feed found, English or vernacular.

    {
        "name": "TOI Ahmedabad (Gujarat)",
        "slug": "toi-ahmedabad",
        "feed_url": "https://timesofindia.indiatimes.com/rssfeeds/-2128821153.cms",
        "homepage_url": "https://timesofindia.indiatimes.com/city/ahmedabad",
        "category": "regional_west",
        "region": "west"
    },
    {
        "name": "TOI Surat (Gujarat)",
        "slug": "toi-surat",
        "feed_url": "https://timesofindia.indiatimes.com/rssfeeds/3942660.cms",
        "homepage_url": "https://timesofindia.indiatimes.com/city/surat",
        "category": "regional_west",
        "region": "west"
    },
    {
        "name": "TOI Vadodara (Gujarat)",
        "slug": "toi-vadodara",
        "feed_url": "https://timesofindia.indiatimes.com/rssfeeds/3942666.cms",
        "homepage_url": "https://timesofindia.indiatimes.com/city/vadodara",
        "category": "regional_west",
        "region": "west"
    },
    {
        "name": "TOI Rajkot (Gujarat)",
        "slug": "toi-rajkot",
        "feed_url": "https://timesofindia.indiatimes.com/rssfeeds/3942663.cms",
        "homepage_url": "https://timesofindia.indiatimes.com/city/rajkot",
        "category": "regional_west",
        "region": "west"
    },
    {
        "name": "TOI Goa",
        "slug": "toi-goa",
        "feed_url": "https://timesofindia.indiatimes.com/rssfeeds/3012535.cms",
        "homepage_url": "https://timesofindia.indiatimes.com/city/goa",
        "category": "regional_west",
        "region": "west"
    },
    {
        "name": "TOI Jaipur (Rajasthan)",
        "slug": "toi-jaipur",
        "feed_url": "https://timesofindia.indiatimes.com/rssfeeds/3012544.cms",
        "homepage_url": "https://timesofindia.indiatimes.com/city/jaipur",
        "category": "regional_north",
        "region": "north"
    },
    {
        "name": "TOI Patna (Bihar)",
        "slug": "toi-patna",
        "feed_url": "https://timesofindia.indiatimes.com/rssfeeds/-2128817995.cms",
        "homepage_url": "https://timesofindia.indiatimes.com/city/patna",
        "category": "regional_east",
        "region": "east"
    },
    {
        "name": "TOI Ranchi (Jharkhand)",
        "slug": "toi-ranchi",
        "feed_url": "https://timesofindia.indiatimes.com/rssfeeds/4118245.cms",
        "homepage_url": "https://timesofindia.indiatimes.com/city/ranchi",
        "category": "regional_east",
        "region": "east"
    },

    # --- Added 2026-08-25: international business/finance/crypto pass,
    # from a user-supplied outlet list, verified live before adding.
    # Skipped: Kitco Metals (404, dead endpoint) and the coindesk.com
    # ?outputType=xml URL (duplicate content of the coindesk.com feed
    # already seeded above). Reuters/Bloomberg have no native public RSS
    # anymore, so they're wrapped via Google News RSS search — note their
    # <link> is a news.google.com redirect, not a direct reuters.com/
    # bloomberg.com URL, so anything downstream that expects a direct
    # article link needs to resolve that redirect.

    {
        "name": "CNBC Top News",
        "slug": "cnbc-top-news",
        "feed_url": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
        "homepage_url": "https://www.cnbc.com",
        "category": "business",
        "region": "national"
    },
    {
        "name": "CNBC Finance",
        "slug": "cnbc-finance",
        "feed_url": "https://www.cnbc.com/id/10000664/device/rss/rss.html",
        "homepage_url": "https://www.cnbc.com/finance/",
        "category": "business",
        "region": "national"
    },
    {
        "name": "Yahoo Finance",
        "slug": "yahoo-finance",
        "feed_url": "https://finance.yahoo.com/news/rssindex",
        "homepage_url": "https://finance.yahoo.com",
        "category": "business",
        "region": "national"
    },
    {
        "name": "MarketWatch Top Stories",
        "slug": "marketwatch-top",
        "feed_url": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
        "homepage_url": "https://www.marketwatch.com",
        "category": "business",
        "region": "national"
    },
    {
        "name": "MarketWatch Breaking News",
        "slug": "marketwatch-bulletins",
        "feed_url": "https://feeds.content.dowjones.io/public/rss/mw_bulletins",
        "homepage_url": "https://www.marketwatch.com",
        "category": "business",
        "region": "national"
    },
    {
        "name": "OilPrice.com",
        "slug": "oilprice",
        "feed_url": "https://oilprice.com/rss/main",
        "homepage_url": "https://oilprice.com",
        "category": "business",
        "region": "national"
    },
    {
        "name": "Reuters (via Google News)",
        "slug": "reuters-gnews",
        "feed_url": "https://news.google.com/rss/search?q=site:reuters.com+when:1d&hl=en-US&gl=US&ceid=US:en",
        "homepage_url": "https://www.reuters.com",
        "category": "world",
        "region": "national"
    },
    {
        "name": "Bloomberg (via Google News)",
        "slug": "bloomberg-gnews",
        "feed_url": "https://news.google.com/rss/search?q=site:bloomberg.com+when:1d&hl=en-US&gl=US&ceid=US:en",
        "homepage_url": "https://www.bloomberg.com",
        "category": "business",
        "region": "national"
    },
    {
        "name": "Decrypt",
        "slug": "decrypt",
        "feed_url": "https://decrypt.co/feed",
        "homepage_url": "https://decrypt.co",
        "category": "crypto",
        "region": "national"
    },

    # --- Added 2026-08-25: second pass on Madhya Pradesh / Chhattisgarh
    # after the first pass came up empty. ABP Live's per-state Hindi feeds
    # are live and genuinely state-specific (verified by inspecting article
    # links/titles, not just HTTP 200). Rejected this pass: News18 Hindi
    # (400 — no such feed path), Zee Hindi (403), ABP CG/MP English
    # equivalents don't exist (Hindi-only), ETV Bharat (410 gone),
    # Navbharat Times, Patrika, Jagran (404 — no working RSS path found),
    # Free Press Journal MP/Bhopal (200 but 0 items).

    {
        "name": "ABP Live (Madhya Pradesh)",
        "slug": "abp-live-mp",
        "feed_url": "https://www.abplive.com/states/madhya-pradesh/feed",
        "homepage_url": "https://www.abplive.com/states/madhya-pradesh",
        "category": "regional_central",
        "region": "central"
    },
    {
        "name": "ABP Live (Chhattisgarh)",
        "slug": "abp-live-cg",
        "feed_url": "https://www.abplive.com/states/chhattisgarh/feed",
        "homepage_url": "https://www.abplive.com/states/chhattisgarh",
        "category": "regional_central",
        "region": "central"
    },

    # --- Added 2026-08-25: northeast expansion pass. Prior northeast
    # coverage was only 3 sources (EastMojo, Assam Tribune, Northeast
    # Today), all Assam-centric. Filled in Nagaland, Meghalaya, Manipur,
    # Arunachal Pradesh — verified live with genuinely local article
    # content, not just HTTP 200. Nagaland Post is real and updates
    # frequently (500 items) but its feed interleaves generic wire/
    # entertainment content with local Nagaland news — kept in since the
    # local content is real, but it's noisier than the others. Tripura,
    # Mizoram, and Sikkim remain uncovered after two search passes: tried
    # Tripura Infoway/Chronicle/Star, Eastern Herald (403/404/0 items),
    # The Zozam Times, Zonet, Mizzima (403/unreachable), Summit Times,
    # Sikkim Chronicle/Express, NowTSikkim (all unreachable/DNS failures).

    {
        "name": "TOI Guwahati (Assam)",
        "slug": "toi-guwahati",
        "feed_url": "https://timesofindia.indiatimes.com/rssfeeds/4118215.cms",
        "homepage_url": "https://timesofindia.indiatimes.com/city/guwahati",
        "category": "northeast",
        "region": "northeast"
    },
    {
        "name": "Northeast Now",
        "slug": "northeast-now",
        "feed_url": "https://nenow.in/feed",
        "homepage_url": "https://nenow.in",
        "category": "northeast",
        "region": "northeast"
    },
    {
        "name": "Morung Express (Nagaland)",
        "slug": "morung-express",
        "feed_url": "https://morungexpress.com/feed",
        "homepage_url": "https://morungexpress.com",
        "category": "northeast",
        "region": "northeast"
    },
    {
        # Real and frequently updated, but the feed interleaves generic
        # wire/entertainment items with genuine Nagaland-local news.
        "name": "Nagaland Post",
        "slug": "nagaland-post",
        "feed_url": "https://www.nagalandpost.com/feed",
        "homepage_url": "https://www.nagalandpost.com",
        "category": "northeast",
        "region": "northeast"
    },
    {
        "name": "The Shillong Times (Meghalaya)",
        "slug": "shillong-times",
        "feed_url": "https://www.theshillongtimes.com/feed",
        "homepage_url": "https://www.theshillongtimes.com",
        "category": "northeast",
        "region": "northeast"
    },
    {
        "name": "Imphal Times (Manipur)",
        "slug": "imphal-times",
        "feed_url": "https://www.imphaltimes.com/feed",
        "homepage_url": "https://www.imphaltimes.com",
        "category": "northeast",
        "region": "northeast"
    },
    {
        "name": "Arunachal24",
        "slug": "arunachal24",
        "feed_url": "https://arunachal24.in/feed",
        "homepage_url": "https://arunachal24.in",
        "category": "northeast",
        "region": "northeast"
    },
    {
        # Verified 2026-08-25: robots.txt allows all (Disallow: empty),
        # feed is live with genuinely local Guwahati/Assam content.
        "name": "Guwahati Plus",
        "slug": "guwahati-plus",
        "feed_url": "https://guwahatiplus.com/feed",
        "homepage_url": "https://guwahatiplus.com",
        "category": "northeast",
        "region": "northeast"
    },
    {
        # Verified 2026-08-27: 71 items live, mixed general-news video feed
        # (og:video / JW-style embeds resolve via the same extractor path
        # used for The Hindu Videos).
        "name": "Hindustan Times Videos",
        "slug": "hindustan-times-videos",
        "feed_url": "https://www.hindustantimes.com/feeds/rss/videos/rssfeed.xml",
        "homepage_url": "https://www.hindustantimes.com/videos",
        "category": "national",
        "region": "national"
    },
    {
        # Verified 2026-08-27: 35 items live, business/markets explainer
        # video feed.
        "name": "Livemint Videos",
        "slug": "livemint-videos",
        "feed_url": "https://www.livemint.com/rss/videos",
        "homepage_url": "https://www.livemint.com/videos",
        "category": "business",
        "region": "national"
    },
    {
        # Verified 2026-08-27: 13 items live, genuinely video-only feed
        # (medicaldialogues.in/videos and /mdtv/ paths).
        "name": "Medical Dialogues Videos",
        "slug": "medical-dialogues-videos",
        "feed_url": "https://medicaldialogues.in/rss/videos",
        "homepage_url": "https://medicaldialogues.in/videos",
        "category": "health",
        "region": "national"
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
