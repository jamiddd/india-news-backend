"""
Backfill: re-scrape existing articles so they pick up video extraction that
landed in extractor.py after they were ingested, without waiting for their
source's next natural RSS poll (which, for a low-volume feed, could be days).

Run it after any change to what extract_full_content() can resolve — e.g.
when Brightcove support was added (Al Jazeera, source_id 80), and again for
the YouTube Shorts flag + duration this now also writes.

Only video fields are touched — content and image_url are left alone, so
this can't regress a good scrape. Safe to re-run: a second pass resolves the
same videos and writes the same values.

Scope is by source, because most sources never carry video and re-fetching
all of them would be wasted work. Pass source ids explicitly, or let --auto
discover every source that has ever produced a video.

Usage:
    python3 scripts/rescrape_video_urls.py 113 114
    python3 scripts/rescrape_video_urls.py --auto --dry-run
    python3 scripts/rescrape_video_urls.py --auto --days 30
"""
import argparse
import asyncio
import logging
import os
import sys
from datetime import timedelta

from curl_cffi.requests import AsyncSession as CurlAsyncSession

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import distinct, or_, select, update

from app.database import AsyncSessionLocal
from app.models import Article, Source, utc_now
from app.services.extractor import extract_full_content, is_youtube_video_url

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CONCURRENCY = 5
# Mirrors extractor._YOUTUBE_CONTENT_URL_RE's accepted forms; SQL LIKE has no
# alternation, so each is matched separately.
YOUTUBE_URL_PATTERNS = [
    "%youtube.com/embed/%",
    "%youtube.com/watch?v=%",
    "%youtube.com/shorts/%",
    "%youtu.be/%",
]


async def discover_source_ids(session) -> list[int]:
    """Every source that has ever produced a video of any kind."""
    result = await session.execute(
        select(distinct(Article.source_id)).where(Article.video_url.isnot(None))
    )
    return [row[0] for row in result.all() if row[0] is not None]


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source_ids", type=int, nargs="*", help="Source ids to re-scrape.")
    parser.add_argument("--auto", action="store_true", help="Discover sources that have produced video.")
    parser.add_argument("--days", type=int, help="Only re-scrape articles published within this many days.")
    parser.add_argument(
        "--missing-only",
        action="store_true",
        help="Only articles with no video_url yet (the old default).",
    )
    parser.add_argument(
        "--annotate-only",
        action="store_true",
        help="Only YouTube videos still missing their Shorts flag. Cheapest mode by far.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report what would change without writing.")
    args = parser.parse_args()

    if not args.source_ids and not args.auto:
        parser.error("pass at least one source id, or --auto")

    async with AsyncSessionLocal() as session:
        source_ids = args.source_ids or await discover_source_ids(session)
        if not source_ids:
            logger.info("No sources with video found.")
            return

        names = dict(
            (await session.execute(select(Source.id, Source.name).where(Source.id.in_(source_ids)))).all()
        )
        logger.info("Scope: " + ", ".join(f"{names.get(i, i)} ({i})" for i in sorted(source_ids)))

        filters = [Article.source_id.in_(source_ids)]
        if args.annotate_only:
            # Just the rows that have a YouTube video and no Shorts flag yet.
            # This is the mode to use for backfilling that flag: the default
            # below matches every article that has no video *at all* (their
            # video_is_short is trivially NULL), which for a publisher like
            # Free Press Journal means re-fetching ~1900 pages to update a
            # handful — enough load, repeated, to get us throttled by the very
            # site we're trying to read.
            filters.append(Article.video_url.isnot(None))
            filters.append(Article.video_is_short.is_(None))
            filters.append(
                or_(*(Article.video_url.like(p) for p in YOUTUBE_URL_PATTERNS))
            )
        elif args.missing_only:
            filters.append(Article.video_url.is_(None))
        else:
            filters.append(
                or_(Article.video_url.is_(None), Article.video_is_short.is_(None))
            )
        if args.days:
            filters.append(Article.published_at >= utc_now() - timedelta(days=args.days))

        rows = (await session.execute(select(Article.id, Article.url, Article.title).where(*filters))).all()
        logger.info(f"Found {len(rows)} article(s) to re-scrape.")

        semaphore = asyncio.Semaphore(CONCURRENCY)
        failures = 0

        # Extraction fans out; the database writes do not. An AsyncSession is
        # not safe to use from several coroutines at once — concurrent
        # execute() calls on one session raise "another operation is in
        # progress", and because that propagates out of gather() it skips the
        # commit at the end, silently discarding the whole run's work. So the
        # workers only fetch, and every write happens in the sequential loop
        # below.
        async def resolve(article_id: int, url: str, title: str):
            nonlocal failures
            async with semaphore:
                try:
                    return article_id, url, await extract_full_content(client, url, title)
                except Exception as e:
                    failures += 1
                    logger.warning(f"  extraction raised for {url}: {e}")
                    return article_id, url, None

        async with CurlAsyncSession() as client:
            resolved = await asyncio.gather(*(resolve(r.id, r.url, r.title) for r in rows))

        found = 0
        for article_id, url, extraction in resolved:
            video_url = extraction.og_video_url if extraction else None
            if not video_url:
                continue
            found += 1
            # Mirrors poller.py: a YouTube video is never media_type "video",
            # since the app renders it as an image card with a badge rather
            # than playing it inline.
            is_youtube = is_youtube_video_url(video_url)
            logger.info(
                f"  video: {url} -> {video_url}"
                + (f" (short={extraction.video_is_short}, {extraction.video_duration_seconds}s)" if is_youtube else "")
            )
            if args.dry_run:
                continue
            values = {
                "video_url": video_url,
                "video_is_short": extraction.video_is_short,
                "video_duration_seconds": extraction.video_duration_seconds,
            }
            if not is_youtube:
                values["media_type"] = "video"
            await session.execute(update(Article).where(Article.id == article_id).values(**values))

        if failures:
            logger.warning(f"{failures} article(s) failed extraction outright.")

        if args.dry_run:
            logger.info(f"Dry run: would have resolved video on {found} of {len(rows)} article(s).")
            return

        await session.commit()
        logger.info(f"Done. Wrote video fields on {found} of {len(rows)} article(s).")


if __name__ == "__main__":
    asyncio.run(main())
