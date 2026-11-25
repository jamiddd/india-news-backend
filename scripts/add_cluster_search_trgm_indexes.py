"""
Migration: trigram indexes so story-cluster text search stops scanning the table.

search_clusters() (app/services/cluster_search.py) matches with
`headline ILIKE '%q%' OR summary ILIKE '%q%'` ordered by last_updated_at. A
btree cannot serve a leading-wildcard ILIKE, so every uncached search read the
whole story_clusters table (~230 MB per call, ~19 s mean on the Micro
instance, 39 calls in under an hour on 2026-11-25). That is the main source of
read IO, and the database is IO-throttled, not CPU-bound.

pg_trgm GIN indexes let Postgres find candidates from the index. They serve
ILIKE with no change to the query or to results. Expect roughly 30 MB for
headline and 100-150 MB for summary.

Both indexes are built CONCURRENTLY so writes keep flowing. That statement
cannot run inside a transaction, and a long build must not hit the pooler's
statement_timeout. A transaction-mode pooler connection can't hold either
guarantee, so this connects through the SESSION-mode pooler port (5432) and
disables the timeout on that one connection.

Building them reads the whole table once, so run it when the Disk IO budget has
recovered (Supabase dashboard -> Reports -> Database), not during an incident.

Usage (from backend/, with DATABASE_URL set):
    python3 scripts/add_cluster_search_trgm_indexes.py --check   # report only
    python3 scripts/add_cluster_search_trgm_indexes.py
"""
import asyncio
import os
import re
import sys

import asyncpg

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

EXTENSION_SQL = "CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA extensions"

INDEXES = {
    "idx_clusters_headline_trgm": (
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_clusters_headline_trgm "
        "ON story_clusters USING gin (headline extensions.gin_trgm_ops)"
    ),
    "idx_clusters_summary_trgm": (
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_clusters_summary_trgm "
        "ON story_clusters USING gin (summary extensions.gin_trgm_ops)"
    ),
}


def _dsn() -> str:
    url = os.environ["DATABASE_URL"]
    url = re.sub(r"^postgresql\+asyncpg", "postgresql", url)
    url = re.sub(r"[?&]ssl=require", "", url)
    # Session-mode pooler port: the setting below persists for the connection.
    return url.replace(":6543/", ":5432/")


async def main(check_only: bool) -> int:
    conn = await asyncpg.connect(_dsn(), ssl="require", timeout=60)
    try:
        existing = {
            r["indexname"]
            for r in await conn.fetch(
                "SELECT indexname FROM pg_indexes WHERE tablename = 'story_clusters'"
            )
        }
        for name in INDEXES:
            print(f"{name}: {'present' if name in existing else 'missing'}")
        if check_only:
            return 0

        await conn.execute("SET statement_timeout = 0")
        await conn.execute(EXTENSION_SQL)
        for name, sql in INDEXES.items():
            if name in existing:
                continue
            print(f"building {name} ...", flush=True)
            await conn.execute(sql)
            print(f"built {name}")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main("--check" in sys.argv)))
