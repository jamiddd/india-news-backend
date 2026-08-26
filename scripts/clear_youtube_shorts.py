"""
One-off cleanup: null out video_url/media_type for already-stored articles
whose video_url is a YouTube embed that turns out to be a Short.

Written after discovering YouTube Shorts frequently fail to play through
the standard /embed/<id> IFrame player even though they're not deleted —
see extractor.py's _is_youtube_short, added after this was already ingested
into the DB (so it only helps sources scraped *after* that fix — this
one-off cleans up what came in before it, letting those rows fall back to
their text/image the way the article would look if it had never had video).

Scoped to video_url LIKE 'youtube.com/embed/%' rather than a blanket scan —
that pattern is only ever written by the YouTube fallback, so it can't
misfire on a Brightcove/JW/native-<video> URL.

Usage:
    python3 scripts/clear_youtube_shorts.py
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
from app.services.extractor import _is_youtube_short

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CONCURRENCY = 5


async def main():
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Article.id, Article.video_url).where(Article.video_url.like("%youtube.com/embed/%"))
        )
        rows = result.all()
        logger.info(f"Found {len(rows)} YouTube-embed articles to check.")

        semaphore = asyncio.Semaphore(CONCURRENCY)
        cleared = 0

        async with CurlAsyncSession() as client:
            async def check(article_id: int, video_url: str):
                nonlocal cleared
                video_id = video_url.rstrip("/").split("/embed/")[-1].split("?")[0]
                async with semaphore:
                    is_short = await _is_youtube_short(client, video_id)
                if is_short:
                    await session.execute(
                        update(Article).where(Article.id == article_id).values(video_url=None, media_type=None)
                    )
                    cleared += 1
                    logger.info(f"  cleared (Short): {video_url}")

            await asyncio.gather(*(check(r.id, r.video_url) for r in rows))

        await session.commit()
        logger.info(f"Done. Cleared {cleared} of {len(rows)} YouTube-embed articles (Shorts).")


if __name__ == "__main__":
    asyncio.run(main())
