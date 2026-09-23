"""
One-off backfill: null out Article.image_url wherever it's India Today's
"PTI: International" branded placeholder card (see
is_generic_branded_placeholder() in image_extractor.py) rather than a real
per-story photo.

This is a different failure mode from backfill_placeholder_images.py's:
that script catches a URL reused verbatim across articles, but this
template's filename embeds a fresh numeric id on every upload, so no two
articles ever share the literal URL and the reuse-count check never fires.
Existing rows were ingested before is_generic_branded_placeholder() existed,
so this card may already be sitting in the DB as if it were a real image.

Only clears Article.image_url — mirrors backfill_placeholder_images.py in
leaving image_urls/media_type untouched (image_url is always image_urls[0]
when present, and callers already tolerate a null image_url in that array).

Safe to re-run any time (idempotent: rows already null are left untouched).

Usage:
    python3 scripts/backfill_generic_branded_placeholder_images.py [--dry-run]
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select, update

from app.database import AsyncSessionLocal
from app.models import Article
from app.services.image_extractor import is_generic_branded_placeholder

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main():
    dry_run = "--dry-run" in sys.argv
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Article.id, Article.source_id, Article.image_url)
            .where(Article.image_url.ilike("%pti-international-%"))
        )
        rows = result.all()
        offenders = [
            (article_id, source_id, image_url)
            for article_id, source_id, image_url in rows
            if is_generic_branded_placeholder(image_url)
        ]
        logger.info(f"Found {len(offenders)} article(s) with the generic branded placeholder card.")

        for article_id, source_id, image_url in offenders:
            logger.info(f"  article_id={article_id} source_id={source_id}: {image_url}")
            if not dry_run:
                await session.execute(
                    update(Article).where(Article.id == article_id).values(image_url=None)
                )

        if dry_run:
            logger.info("Dry run — no rows changed.")
        else:
            await session.commit()
            logger.info(f"Done. Cleared image_url on {len(offenders)} article rows.")


if __name__ == "__main__":
    asyncio.run(main())
