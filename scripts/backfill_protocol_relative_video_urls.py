"""
One-off backfill: give a stored protocol-relative video_url (//host/path) an
explicit https: scheme, in place.

These rows were written by the pre-fix _extract_video_object_media(), which
returned a VideoObject ld+json contentUrl as-is instead of running it through
absolutize_scheme() the way _extract_og_video() already did (see
app/services/extractor.py). Times Now's video pages are the known case
(contentUrl like //sw-a.akamaized.net/...) — the app's player can't open a
scheme-less URI and reports a 0:00 duration.

No re-scrape needed: absolutize_scheme() is a pure string transform of the
value already stored, so this just re-applies it in place. Safe to re-run
(idempotent: rows already schemed are left untouched).

Usage:
    python3 scripts/backfill_protocol_relative_video_urls.py --dry-run
    python3 scripts/backfill_protocol_relative_video_urls.py
"""
import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database import admin_engine
from app.models import Article, Source
from app.services.extractor import absolutize_scheme

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main(dry_run: bool):
    engine = admin_engine()
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with session_factory() as session:
            result = await session.execute(
                select(Article.id, Source.name.label("source_name"), Article.video_url)
                .join(Source, Source.id == Article.source_id)
                .where(Article.video_url.like("//%"))
            )
            rows = result.all()
            logger.info(f"Found {len(rows)} articles with a protocol-relative video_url.")

            by_source: dict[str, int] = {}
            for row in rows:
                by_source[row.source_name] = by_source.get(row.source_name, 0) + 1
                fixed = absolutize_scheme(row.video_url)
                logger.info(f"  [{row.id}] {row.source_name}: {row.video_url} -> {fixed}")
                if not dry_run:
                    await session.execute(
                        update(Article).where(Article.id == row.id).values(video_url=fixed)
                    )

            if by_source:
                logger.info("By source: " + ", ".join(f"{k}={v}" for k, v in sorted(by_source.items(), key=lambda kv: -kv[1])))

            if dry_run:
                logger.info(f"Dry run — would update {len(rows)} rows. Re-run without --dry-run to apply.")
            else:
                await session.commit()
                logger.info(f"Done. Updated {len(rows)} rows.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Log what would change without writing")
    args = parser.parse_args()
    asyncio.run(main(args.dry_run))
