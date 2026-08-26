"""
One-off fix: upgrade already-stored video_url values from http:// to
https:// in place, for rows written before the Brightcove resolver started
forcing https (see extractor.py's _resolve_brightcove_video) — those rows
have a permanently-unplayable cleartext URL (the app's
usesCleartextTraffic is false) that a plain re-scrape wouldn't touch, since
rescrape_video_urls.py only targets video_url IS NULL rows.

A straight string substitution (rather than a re-scrape) is safe here
because the CDN (boltdns.net) serves the identical manifest on both
schemes — verified by hand before writing this script.

Usage:
    python3 scripts/fix_video_url_scheme.py <source_id> [<source_id> ...]
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select, update

from app.database import AsyncSessionLocal
from app.models import Article

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main(source_ids: list[int]):
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Article.id, Article.video_url)
            .where(Article.source_id.in_(source_ids), Article.video_url.like("http://%"))
        )
        rows = result.all()
        logger.info(f"Found {len(rows)} articles with a cleartext video_url for sources {source_ids}.")

        for row in rows:
            fixed = "https://" + row.video_url[len("http://"):]
            await session.execute(update(Article).where(Article.id == row.id).values(video_url=fixed))

        await session.commit()
        logger.info(f"Done. Upgraded {len(rows)} video_url values to https://.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/fix_video_url_scheme.py <source_id> [<source_id> ...]")
        sys.exit(1)
    asyncio.run(main([int(a) for a in sys.argv[1:]]))
