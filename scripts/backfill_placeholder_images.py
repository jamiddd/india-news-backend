"""
One-off backfill: null out Article.image_url wherever a source has already
reused the exact same image URL across PLACEHOLDER_REUSE_THRESHOLD-or-more
other articles (see is_placeholder_image() in image_extractor.py).

Existing rows were ingested before that detection existed, so publisher-wide
default/logo thumbnails (e.g. a generic section image The Hindu falls back to
when a story has no dedicated photo) may already be sitting in the DB,
displayed as if they were real per-story images. This finds and clears them
in place — no network fetch needed, just a self-join over what's already
stored.

Safe to re-run any time PLACEHOLDER_REUSE_THRESHOLD changes (idempotent: rows
already null are left untouched).

Usage:
    python3 scripts/backfill_placeholder_images.py [--dry-run]
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select, update, func

from app.database import AsyncSessionLocal
from app.models import Article
from app.services.image_extractor import PLACEHOLDER_REUSE_THRESHOLD

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main():
    dry_run = "--dry-run" in sys.argv
    async with AsyncSessionLocal() as session:
        # Find (source_id, image_url) pairs reused across enough distinct
        # articles from the same source to count as a placeholder.
        result = await session.execute(
            select(Article.source_id, Article.image_url, func.count(Article.id).label("cnt"))
            .where(Article.image_url.isnot(None))
            .group_by(Article.source_id, Article.image_url)
            .having(func.count(Article.id) >= PLACEHOLDER_REUSE_THRESHOLD)
        )
        offenders = result.all()
        logger.info(f"Found {len(offenders)} (source, image_url) pairs reused >= {PLACEHOLDER_REUSE_THRESHOLD}x — treating as placeholders.")

        total_cleared = 0
        for source_id, image_url, cnt in offenders:
            logger.info(f"  source_id={source_id} reused {cnt}x: {image_url}")
            if not dry_run:
                res = await session.execute(
                    update(Article)
                    .where(Article.source_id == source_id, Article.image_url == image_url)
                    .values(image_url=None)
                )
                total_cleared += res.rowcount or 0

        if dry_run:
            logger.info("Dry run — no rows changed.")
        else:
            await session.commit()
            logger.info(f"Done. Cleared image_url on {total_cleared} article rows.")


if __name__ == "__main__":
    asyncio.run(main())
