"""
One-off backfill: re-scrape existing articles that predate a video-extraction
capability landing in extractor.py, so they pick up video_url/media_type
without waiting for their source's next natural RSS poll (which, for a
low-volume/slow-moving feed, could be days).

Scoped by source_id rather than a blanket "video_url IS NULL" scan across
every article — most sources never carry video and re-fetching all of them
would be wasted work. Pass the source ids that just gained (or regained)
video-extraction support, e.g. after adding Brightcove support this was run
for Al Jazeera (source_id 80).

Usage:
    python3 scripts/rescrape_video_urls.py <source_id> [<source_id> ...]
"""
import asyncio
import logging
import os
import sys

from curl_cffi.requests import AsyncSession as CurlAsyncSession

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select, update

from app.database import AsyncSessionLocal
from app.models import Article
from app.services.extractor import extract_full_content

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CONCURRENCY = 5


async def main(source_ids: list[int]):
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Article.id, Article.title, Article.url)
            .where(Article.source_id.in_(source_ids), Article.video_url.is_(None))
        )
        rows = result.all()
        logger.info(f"Found {len(rows)} articles to re-scrape for sources {source_ids}.")

        semaphore = asyncio.Semaphore(CONCURRENCY)
        found = 0

        async with CurlAsyncSession() as client:
            async def rescrape(article_id: int, title: str, url: str):
                nonlocal found
                async with semaphore:
                    extraction = await extract_full_content(client, url, title)
                if extraction.og_video_url:
                    await session.execute(
                        update(Article)
                        .where(Article.id == article_id)
                        .values(video_url=extraction.og_video_url, media_type="video")
                    )
                    found += 1
                    logger.info(f"  video found: {url}")

            await asyncio.gather(*(rescrape(r.id, r.title, r.url) for r in rows))

        await session.commit()
        logger.info(f"Done. {found} of {len(rows)} articles now have video_url set.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/rescrape_video_urls.py <source_id> [<source_id> ...]")
        sys.exit(1)
    asyncio.run(main([int(a) for a in sys.argv[1:]]))
