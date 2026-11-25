"""
Migration: move story-cluster search text into its own table.

Why: add_cluster_search_trgm_indexes.py put two pg_trgm GIN indexes on
story_clusters, which fixed slow ILIKE search (~230 MB scanned per query) but made
every headline_score rewrite expensive. headline_score is itself indexed, so
its updates can never be HOT: Postgres writes a new entry into EVERY index on
the row, and a GIN trigram entry for a headline+summary is hundreds of keys.
Measured 2026-11-25: ~28 KB of WAL per rewritten row (vs ~2.6 KB before the
GIN indexes), ~55-75 MB per poll cycle, 4-5 GB/day, on a 1 GB / 11 MB/s
Micro instance.

This script gives search its own narrow table, cluster_search(cluster_id,
headline, summary), puts the trigram indexes there, and keeps it in sync with a
trigger that fires ONLY when headline or summary actually changes. Score updates
never touch it.

Order of operations (each step is idempotent; re-running resumes):
  1. create the table
  2. create the sync function and the INSERT / UPDATE triggers FIRST, so no
     change made during the backfill is missed
  3. backfill in id-range batches (ON CONFLICT DO NOTHING, so the triggers
     and the backfill never fight), pausing between batches to spare the disk
  4. build the two trigram GIN indexes CONCURRENTLY on the new table
Then deploy the code that reads cluster_search (app/services/cluster_search.py).
The code must NOT go out before step 3 finishes: the table would be empty and
search would return nothing. Finally, as a separate explicit step:
  --drop-old-indexes   drop idx_clusters_headline_trgm / idx_clusters_summary_trgm
                       from story_clusters (run only after the new code is live)

Connects through the SESSION-mode pooler port (5432) so statement_timeout can
be disabled for the long-running statements; see add_cluster_search_trgm_indexes.py.

Usage (from backend/, with DATABASE_URL set):
    python3 scripts/add_cluster_search_table.py --check
    python3 scripts/add_cluster_search_table.py
    python3 scripts/add_cluster_search_table.py --drop-old-indexes
"""
import asyncio
import os
import re
import sys

import asyncpg

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Deliberately gentle: the database was IO-throttled when this was written
# (Disk IO Balance ~22%), so spread the backfill out rather than finishing fast.
BATCH_IDS = 5000
PAUSE_SECONDS = 3.0

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS cluster_search (
    cluster_id integer PRIMARY KEY REFERENCES story_clusters(id) ON DELETE CASCADE,
    headline   text,
    summary    text
)
"""

CREATE_FUNCTION = """
CREATE OR REPLACE FUNCTION cluster_search_sync() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO cluster_search (cluster_id, headline, summary)
    VALUES (NEW.id, NEW.headline, NEW.summary)
    ON CONFLICT (cluster_id)
    DO UPDATE SET headline = EXCLUDED.headline, summary = EXCLUDED.summary;
    RETURN NULL;
END
$$
"""

# CREATE TRIGGER has no IF NOT EXISTS: drop first so re-runs are safe.
TRIGGERS = [
    "DROP TRIGGER IF EXISTS trg_cluster_search_ins ON story_clusters",
    """
    CREATE TRIGGER trg_cluster_search_ins
    AFTER INSERT ON story_clusters
    FOR EACH ROW EXECUTE FUNCTION cluster_search_sync()
    """,
    "DROP TRIGGER IF EXISTS trg_cluster_search_upd ON story_clusters",
    # Fires only when an UPDATE names one of these columns AND the value really
    # changed, so a headline_score-only UPDATE never reaches this table.
    """
    CREATE TRIGGER trg_cluster_search_upd
    AFTER UPDATE OF headline, summary ON story_clusters
    FOR EACH ROW
    WHEN (OLD.headline IS DISTINCT FROM NEW.headline
          OR OLD.summary IS DISTINCT FROM NEW.summary)
    EXECUTE FUNCTION cluster_search_sync()
    """,
]

INDEXES = {
    "idx_cluster_search_headline_trgm": (
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_cluster_search_headline_trgm "
        "ON cluster_search USING gin (headline extensions.gin_trgm_ops)"
    ),
    "idx_cluster_search_summary_trgm": (
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_cluster_search_summary_trgm "
        "ON cluster_search USING gin (summary extensions.gin_trgm_ops)"
    ),
}

OLD_INDEXES = ["idx_clusters_headline_trgm", "idx_clusters_summary_trgm"]


def _dsn() -> str:
    url = os.environ["DATABASE_URL"]
    url = re.sub(r"^postgresql\+asyncpg", "postgresql", url)
    url = re.sub(r"[?&]ssl=require", "", url)
    return url.replace(":6543/", ":5432/")


async def _report(conn) -> None:
    exists = await conn.fetchval("SELECT to_regclass('public.cluster_search') IS NOT NULL")
    print(f"table cluster_search: {'present' if exists else 'missing'}")
    trg = {r["tgname"] for r in await conn.fetch(
        "SELECT tgname FROM pg_trigger WHERE tgrelid = 'story_clusters'::regclass AND NOT tgisinternal")}
    for name in ("trg_cluster_search_ins", "trg_cluster_search_upd"):
        print(f"{name}: {'present' if name in trg else 'missing'}")
    if exists:
        have = await conn.fetchval("SELECT count(*) FROM cluster_search")
        total = await conn.fetchval("SELECT count(*) FROM story_clusters")
        print(f"rows: {have} / {total} story_clusters")
    idx = {r["indexname"] for r in await conn.fetch("SELECT indexname FROM pg_indexes")}
    for name in [*INDEXES, *OLD_INDEXES]:
        print(f"{name}: {'present' if name in idx else 'missing'}")


async def _backfill(conn) -> None:
    lo_id, hi_id = await conn.fetchrow("SELECT min(id), max(id) FROM story_clusters")
    if lo_id is None:
        return
    start = lo_id
    inserted = 0
    while start <= hi_id:
        end = start + BATCH_IDS
        status = await conn.execute(
            "INSERT INTO cluster_search (cluster_id, headline, summary) "
            "SELECT id, headline, summary FROM story_clusters WHERE id >= $1 AND id < $2 "
            "ON CONFLICT (cluster_id) DO NOTHING",
            start, end,
        )
        inserted += int(status.split()[-1])
        print(f"backfilled ids {start}..{end - 1} (total inserted {inserted})", flush=True)
        start = end
        await asyncio.sleep(PAUSE_SECONDS)


async def main(check_only: bool, drop_old: bool) -> int:
    conn = await asyncpg.connect(_dsn(), ssl="require", timeout=60)
    try:
        if check_only:
            await _report(conn)
            return 0

        await conn.execute("SET statement_timeout = 0")

        if drop_old:
            for name in OLD_INDEXES:
                print(f"dropping {name} ...", flush=True)
                await conn.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
            await _report(conn)
            return 0

        await conn.execute(CREATE_TABLE)
        await conn.execute(CREATE_FUNCTION)
        for stmt in TRIGGERS:
            await conn.execute(stmt)
        print("table and triggers in place; backfilling ...", flush=True)
        await _backfill(conn)
        for name, sql in INDEXES.items():
            print(f"building {name} ...", flush=True)
            await conn.execute(sql)
        await _report(conn)
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main("--check" in sys.argv, "--drop-old-indexes" in sys.argv)))
