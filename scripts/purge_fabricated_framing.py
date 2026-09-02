"""Null out framing_comparison on clusters that never had two outlets to compare.

Measured on production 2026-09-02: 47,018 clusters carried a
framing_comparison while only 418 had 2+ distinct sources. The rest are
fabrications — the rule-based baseline written for every cluster in
enrich_cluster_with_ai(), which then survived because the paid call's
overwrite was guarded by a truthiness check that an explicitly-empty [] fails
(see the comment at that call site). The model was returning the correct
answer and the code was discarding it.

Nulls the column wherever the cluster has fewer than 2 distinct sources, so
the data is honest at rest and cannot leak back into the UI or into any
future feature that reads it. Does not touch entities/topics/summary — those
are legitimate for single-source clusters.

RUN THIS AFTER the clustering fix is live and has been running for a while.
Clustering now merges far more aggressively, so thousands of clusters that
are singletons today will legitimately gain a second source; purging first
just means re-generating them later. Purging is safe either way (a cluster
that later gains a source gets re-enriched), it is only wasted work.

Read-only by default. Pass --apply to write.

Usage:
    python3 scripts/purge_fabricated_framing.py
    python3 scripts/purge_fabricated_framing.py --apply
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import text

from app.database import AsyncSessionLocal

# Counts distinct sources from the articles table rather than trusting
# story_clusters.distinct_source_count, which is a maintained counter and
# therefore the thing most likely to be wrong if anything ever drifted.
#
# `IS NOT NULL` alone is not enough: rows cleared before models.py set
# none_as_null=True hold the JSON value `null`, which is NOT NULL to Postgres.
# Matching them here is deliberate — it normalises those rows to real SQL NULL
# as a side effect, so `IS NULL` means what it says everywhere afterwards.
TARGET_PREDICATE = """
    framing_comparison IS NOT NULL
    AND (
        SELECT count(DISTINCT a.source_id)
        FROM articles a
        WHERE a.cluster_id = story_clusters.id
    ) < 2
"""

# Rows that are already logically empty and only need the representation
# normalised — reported separately so the purge count isn't inflated by them.
JSON_NULL_PREDICATE = """
    framing_comparison IS NOT NULL
    AND json_typeof(framing_comparison) = 'null'
"""


async def main(apply: bool) -> None:
    async with AsyncSessionLocal() as session:
        total_with_framing = (await session.execute(text(
            "SELECT count(*) FROM story_clusters WHERE framing_comparison IS NOT NULL"
        ))).scalar()
        fabricated = (await session.execute(text(
            f"SELECT count(*) FROM story_clusters WHERE {TARGET_PREDICATE}"
        ))).scalar()

        json_nulls = (await session.execute(text(
            f"SELECT count(*) FROM story_clusters WHERE {JSON_NULL_PREDICATE}"
        ))).scalar()

        print(f"clusters with framing_comparison : {total_with_framing}")
        print(f"of those, fewer than 2 sources   : {fabricated}")
        print(f"legitimate (2+ sources), kept    : {total_with_framing - fabricated}")
        print(f"already-empty JSON 'null' rows   : {json_nulls} "
              f"(counted above; normalised to SQL NULL by this run)")

        if not fabricated:
            print("\nNothing to purge.")
            return

        print("\n--- sample of what would be nulled ---")
        rows = (await session.execute(text(
            f"""SELECT id, headline, framing_comparison
                FROM story_clusters WHERE {TARGET_PREDICATE} LIMIT 5"""
        ))).all()
        for cid, headline, framing in rows:
            print(f"\n  [{cid}] {(headline or '')[:90]}")
            print(f"       {str(framing)[:160]}")

        if not apply:
            print(f"\nDry run: {fabricated} row(s) would be nulled. "
                  f"Re-run with --apply to write.")
            return

        result = await session.execute(text(
            f"UPDATE story_clusters SET framing_comparison = NULL WHERE {TARGET_PREDICATE}"
        ))
        await session.commit()
        print(f"\nDone: nulled framing_comparison on {result.rowcount} cluster(s).")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="Write changes (default: dry run)")
    args = ap.parse_args()
    asyncio.run(main(args.apply))
