"""
Re-scrape articles that should have a YouTube video but don't, after the
Shorts filter was removed from extractor.py.

Background: inline YouTube playback was failing with IFrame error 152 ("this
video is unavailable"), which was misdiagnosed as "Shorts don't embed". The
real cause was the app player's embed origin defaulting to youtube.com —
YouTube rejects an embed whose referrer is youtube.com itself. Any genuine
third-party origin plays fine, Shorts included (verified against YouTube's
embed endpoint). So extractor.py's _is_youtube_short skip is gone, and the
rows it (and its one-off cleanup script) dropped video from need re-running
through extraction to get their video_url back.

Only video_url/media_type are touched — content and image_url are left
alone, so this can't regress a good scrape. media_type is only ever
upgraded to "video"; rows where no video is found keep whatever they have.
Safe to re-run: a second pass just finds the same videos and writes the
same values.

Scope defaults to sources that have ever produced a YouTube embed. Sources
whose videos were *entirely* cleared by the old cleanup have no such row
left to be discovered by, so pass them explicitly with --source-id.

Usage:
    python3 scripts/rescrape_youtube_videos.py --dry-run
    python3 scripts/rescrape_youtube_videos.py
    python3 scripts/rescrape_youtube_videos.py --days 30 --source-id 113 --source-id 114
"""
import argparse
import asyncio
import logging
import os
import sys
from datetime import timedelta

from curl_cffi.requests import AsyncSession as CurlAsyncSession

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import distinct, select, update

from app.database import AsyncSessionLocal
from app.models import Article, Source, utc_now
from app.services.extractor import extract_full_content

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CONCURRENCY = 5
YOUTUBE_EMBED_PATTERN = "%youtube.com/embed/%"


async def discover_source_ids(session) -> list[int]:
    """Sources with at least one surviving YouTube-embed article."""
    result = await session.execute(
        select(distinct(Article.source_id)).where(Article.video_url.like(YOUTUBE_EMBED_PATTERN))
    )
    return [row[0] for row in result.all() if row[0] is not None]


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=14, help="How far back to re-scrape (default: 14).")
    parser.add_argument(
        "--source-id",
        type=int,
        action="append",
        dest="source_ids",
        help="Restrict/extend scope to this source id. Repeatable. Defaults to auto-discovery.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report what would change without writing.")
    args = parser.parse_args()

    async with AsyncSessionLocal() as session:
        source_ids = args.source_ids or await discover_source_ids(session)
        if not source_ids:
            logger.info("No sources with YouTube embeds found — pass --source-id explicitly.")
            return

        names = dict(
            (await session.execute(select(Source.id, Source.name).where(Source.id.in_(source_ids)))).all()
        )
        logger.info("Scope: " + ", ".join(f"{names.get(i, i)} ({i})" for i in sorted(source_ids)))

        cutoff = utc_now() - timedelta(days=args.days)
        rows = (
            await session.execute(
                select(Article.id, Article.url, Article.title).where(
                    Article.source_id.in_(source_ids),
                    Article.video_url.is_(None),
                    Article.published_at >= cutoff,
                )
            )
        ).all()
        logger.info(f"Found {len(rows)} video-less articles from the last {args.days} days to re-scrape.")

        semaphore = asyncio.Semaphore(CONCURRENCY)
        recovered = 0

        async with CurlAsyncSession() as client:
            async def rescrape(article_id: int, url: str, title: str):
                nonlocal recovered
                async with semaphore:
                    extraction = await extract_full_content(client, url, title)
                video_url = extraction.og_video_url
                if not video_url:
                    return
                recovered += 1
                logger.info(f"  video: {url} -> {video_url}")
                if args.dry_run:
                    return
                await session.execute(
                    update(Article)
                    .where(Article.id == article_id)
                    .values(video_url=video_url, media_type="video")
                )

            await asyncio.gather(*(rescrape(r.id, r.url, r.title) for r in rows))

        if args.dry_run:
            logger.info(f"Dry run: would have recovered video on {recovered} of {len(rows)} article(s).")
            return

        await session.commit()
        logger.info(f"Done. Recovered video on {recovered} of {len(rows)} article(s).")


if __name__ == "__main__":
    asyncio.run(main())
