"""
One-off backfill: fetch image_width/image_height (see image_extractor.
fetch_image_dimensions) for articles that already sit in a chain fed into the
Timeline/Context tab, so the new HD-first ranking in main.py's
_cluster_to_list_out(image_priority_sort=True) has something to work with
immediately instead of waiting for every member cluster's articles to get
new poller runs (most never will — a chain's older members stop receiving
new articles once the story moves on).

Scope is deliberately narrow: only articles belonging to a cluster referenced
by some story_timeline_features.cluster_ids (active row or within the
archive window) — the full articles table is enormous and the vast majority
of it will never be a timeline hero candidate. Only rows with image_url set
and both dimension columns still NULL are touched, so this is safe to re-run
(a previous partial run, or new chain members picked up since, are the only
rows that do any work the second time).

Usage:
    python3 scripts/backfill_timeline_image_dimensions.py [--dry-run]
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from curl_cffi.requests import AsyncSession as CurlAsyncSession
from sqlalchemy import select, update

from app.database import AsyncSessionLocal
from app.models import Article, StoryTimelineFeature
from app.services.image_extractor import fetch_image_dimensions

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CONCURRENCY = 5


async def main():
    dry_run = "--dry-run" in sys.argv
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(select(StoryTimelineFeature.cluster_ids))
        ).scalars().all()
        cluster_ids = {cid for ids in rows if ids for cid in ids}
        if not cluster_ids:
            logger.info("No timeline chains found — nothing to backfill.")
            return

        articles = (
            await session.execute(
                select(Article.id, Article.image_url).where(
                    Article.cluster_id.in_(cluster_ids),
                    Article.image_url.isnot(None),
                    Article.image_width.is_(None),
                    Article.image_height.is_(None),
                )
            )
        ).all()
        logger.info(f"{len(cluster_ids)} chain-member cluster(s), {len(articles)} article(s) need dimensions.")
        if not articles:
            return

        semaphore = asyncio.Semaphore(CONCURRENCY)

        async def fetch_one(client, article_id, image_url):
            async with semaphore:
                width, height = await fetch_image_dimensions(client, image_url)
                return article_id, width, height

        async with CurlAsyncSession() as client:
            results = await asyncio.gather(
                *(fetch_one(client, article_id, image_url) for article_id, image_url in articles)
            )

        resolved = 0
        for article_id, width, height in results:
            if width is None or height is None:
                continue
            resolved += 1
            logger.info(f"  article_id={article_id}: {width}x{height}")
            if not dry_run:
                await session.execute(
                    update(Article)
                    .where(Article.id == article_id)
                    .values(image_width=width, image_height=height)
                )

        if dry_run:
            logger.info(f"Dry run — resolved {resolved}/{len(articles)}, no rows changed.")
        else:
            await session.commit()
            logger.info(f"Done. Wrote dimensions for {resolved}/{len(articles)} article(s).")


if __name__ == "__main__":
    asyncio.run(main())
