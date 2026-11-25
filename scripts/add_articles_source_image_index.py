"""
Migration: index articles by (source_id, image_url) for the ingest image check.

During ingestion, each new article's image is checked against the same source's
other articles:

    SELECT count(articles.id) FROM articles
    WHERE source_id = $1 AND image_url = $2 AND url_hash != $3

With only ix_articles_source_id to go on, Postgres read every article of the
source (~9,000 rows, ~1,500 blocks) for each lookup, ~1,000s of lookups per poll
cycle. On the IO-throttled Micro instance (2026-11-25) that was about a third of
the remaining reads. A composite index turns each lookup into a couple of page
reads.

The index is partial (image_url IS NOT NULL): the predicate `image_url = value` can
only match non-null rows, so the planner still uses it, and it skips the ~22% of
rows with no image. Built CONCURRENTLY so writes keep flowing, over the SESSION-mode
pooler port (5432) with statement_timeout disabled for this connection; see
add_cluster_search_trgm_indexes.py. Building it reads the whole articles table
once (~520 MB), so run it when the Disk IO budget is not being drained.

Usage (from backend/, with DATABASE_URL set):
    python3 scripts/add_articles_source_image_index.py --check
    python3 scripts/add_articles_source_image_index.py
"""
import asyncio
import os
import re
import sys

import asyncpg

INDEX_NAME = "idx_articles_source_image"
CREATE_SQL = (
    f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX_NAME} "
    "ON articles (source_id, image_url) WHERE image_url IS NOT NULL"
)


def _dsn() -> str:
    url = os.environ["DATABASE_URL"]
    url = re.sub(r"^postgresql\+asyncpg", "postgresql", url)
    url = re.sub(r"[?&]ssl=require", "", url)
    return url.replace(":6543/", ":5432/")


async def main(check_only: bool) -> int:
    conn = await asyncpg.connect(_dsn(), ssl="require", timeout=60)
    try:
        present = await conn.fetchval(
            "SELECT count(*) FROM pg_indexes WHERE indexname = $1", INDEX_NAME
        )
        print(f"{INDEX_NAME}: {'present' if present else 'missing'}")
        if check_only or present:
            return 0
        await conn.execute("SET statement_timeout = 0")
        print(f"building {INDEX_NAME} ...", flush=True)
        await conn.execute(CREATE_SQL)
        size = await conn.fetchval(
            "SELECT pg_size_pretty(pg_relation_size($1::regclass))", INDEX_NAME
        )
        print(f"built {INDEX_NAME} ({size})")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main("--check" in sys.argv)))
