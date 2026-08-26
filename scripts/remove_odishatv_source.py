"""
One-off cleanup: removes the OdishaTV (OTV) source and its articles — its
RSS feed content is low quality (heavy hashtag spam). Already removed from
scripts/seed_sources.py so no new articles get ingested from it going
forward; this script removes what's already in the DB.

Deleting the `sources` row cascades (ON DELETE CASCADE) to delete its
`articles` rows automatically, but that cascade does NOT update
`story_clusters.article_count` / `distinct_source_count`, and sets
`representative_article_id` to NULL on any cluster that pointed at a
deleted OdishaTV article. So after deleting the source, this also removes
any `story_clusters` row left with zero remaining articles (the common
case pre-launch, since there are no users yet to worry about breaking a
saved/notified story).

Safe to run multiple times (no-ops if the source is already gone).

Usage:
    python3 scripts/remove_odishatv_source.py
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import text

from app.database import engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SLUG = "odishatv"


async def main():
    async with engine.begin() as conn:
        source_row = (await conn.execute(
            text("SELECT id, name FROM sources WHERE slug = :slug"), {"slug": SLUG}
        )).fetchone()

        if not source_row:
            logger.info("No source with slug=%r found — already removed.", SLUG)
        else:
            source_id, name = source_row
            article_count = (await conn.execute(
                text("SELECT count(*) FROM articles WHERE source_id = :sid"), {"sid": source_id}
            )).scalar()
            logger.info("Deleting source %r (id=%s) and its %s articles.", name, source_id, article_count)

            await conn.execute(text("DELETE FROM sources WHERE id = :sid"), {"sid": source_id})
            logger.info("Source and its articles deleted.")

        orphaned = await conn.execute(text("""
            DELETE FROM story_clusters
            WHERE id NOT IN (
                SELECT DISTINCT cluster_id FROM articles WHERE cluster_id IS NOT NULL
            )
        """))
        logger.info("Removed %s orphaned story_clusters (0 remaining articles).", orphaned.rowcount)


if __name__ == "__main__":
    asyncio.run(main())
