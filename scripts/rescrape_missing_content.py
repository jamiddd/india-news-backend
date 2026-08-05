"""
One-off recovery script: re-scrape articles whose content is NULL but were
fetched *after* full-article scraping went live (2026-08-05 11:00 UTC).

Needed because a v1 version of content_cleaner.py truncated the entire line
on a boilerplate-marker match, which wiped a handful of articles down to
nothing when trafilatura ran a short paragraph straight into trailing
junk with no line break in between (News18 stubs do this a lot). The fixed
cleaner (see content_cleaner.py) only truncates from the marker onward, but
those articles' raw pre-cleaner text is already gone from the DB — this
re-fetches their source URL instead of trying to recover from stored text.

Scoped to the post-cutoff window so it doesn't try to backfill the ~1000
older articles that predate the scraping feature entirely and never had
content to begin with (re-scraping those is a separate, larger job).

Usage:
    python3 scripts/rescrape_missing_content.py
"""
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone

from curl_cffi.requests import AsyncSession as CurlAsyncSession

# Ensure root of repo/backend is in sys.path (matches other scripts/ here)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select, update

from app.database import AsyncSessionLocal
from app.models import Article
from app.services.extractor import extract_full_content, IMPERSONATE

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CUTOFF = datetime(2026, 8, 5, 11, 0, 0, tzinfo=timezone.utc)
CONCURRENCY = 5


async def main():
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Article.id, Article.title, Article.url)
            .where(Article.content.is_(None), Article.fetched_at >= CUTOFF)
        )
        rows = result.all()
        logger.info(f"Found {len(rows)} articles to re-scrape.")

        semaphore = asyncio.Semaphore(CONCURRENCY)
        recovered = 0

        async with CurlAsyncSession() as client:
            async def rescrape(article_id: int, title: str, url: str):
                nonlocal recovered
                async with semaphore:
                    extraction = await extract_full_content(client, url, title)
                if extraction.content:
                    values = {"content": extraction.content}
                    if extraction.og_image_url:
                        # Only set if we actually found one — these rows may
                        # already have an RSS-provided image_url, and a
                        # missing og:image here shouldn't clobber that.
                        values["image_url"] = extraction.og_image_url
                    await session.execute(update(Article).where(Article.id == article_id).values(**values))
                    recovered += 1

            await asyncio.gather(*(rescrape(r.id, r.title, r.url) for r in rows))

        await session.commit()
        logger.info(f"Done. Recovered content for {recovered} of {len(rows)} articles "
                    f"({len(rows) - recovered} had no extractable content, unchanged).")


if __name__ == "__main__":
    asyncio.run(main())
