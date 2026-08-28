"""
One-off migration for existing deployments: adds `articles.word_count` (see
app/models.py for what it's for — explore_bandit._estimate_word_count reads
this int instead of fetching the whole scraped `content` body just to call
len(.split()) on it, which was pulling article bodies over the wire on every
call on a remote, egress-metered Postgres).

Backfills every existing row with content in one set-based UPDATE using
Postgres's own array_length(regexp_split_to_array(...)), rather than paging
rows into Python — cheap because it computes the count without shipping the
column back out. Rows with no content are left NULL; callers already treat
NULL as "unknown, fall back" (see _estimate_word_count).

Usage:
    python3 scripts/add_word_count_column.py
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


async def main():
    async with engine.begin() as conn:
        await conn.execute(text(
            "ALTER TABLE articles ADD COLUMN IF NOT EXISTS word_count INTEGER"
        ))
        logger.info("articles.word_count column is present.")

        result = await conn.execute(text("""
            UPDATE articles
            SET word_count = GREATEST(
                array_length(regexp_split_to_array(trim(content), '\\s+'), 1),
                1
            )
            WHERE content IS NOT NULL
              AND trim(content) <> ''
              AND word_count IS NULL
        """))
        logger.info(f"Backfilled word_count for {result.rowcount} articles.")


if __name__ == "__main__":
    asyncio.run(main())
