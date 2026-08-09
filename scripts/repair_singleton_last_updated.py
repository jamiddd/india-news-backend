"""
One-off repair: for singleton clusters (article_count == 1), reset
last_updated_at back to first_seen_at wherever the two have diverged.

A singleton cluster only ever gets one Article, added once at creation
(poller.py sets last_updated_at=pub_date == first_seen_at there) — nothing
in the normal ingestion path ever touches last_updated_at again for it,
since that only happens when a *second* article matches into the cluster
(which would also bump article_count past 1). So any singleton whose
last_updated_at has drifted from first_seen_at was touched by something
out-of-band, not real new coverage — confirmed in production on several
crypto clusters from 2022-2025 whose last_updated_at had been bulk-set to
"now", making them rank as if freshly updated in category tabs (ordered by
last_updated_at) and in the "All" feed's headline_score recency-decay term.

See LISTING_MAX_AGE in app/main.py for the accompanying display-layer
backstop (filters listings to first_seen_at within the last N days
regardless of last_updated_at) — this script fixes the underlying data so
headline_score itself stops being inflated for these clusters too.

Safe to re-run (idempotent: rows already matching are left untouched).

Usage:
    python3 scripts/repair_singleton_last_updated.py [--dry-run]
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select, update

from app.database import AsyncSessionLocal
from app.models import StoryCluster


async def main():
    dry_run = "--dry-run" in sys.argv
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(StoryCluster.id, StoryCluster.first_seen_at, StoryCluster.last_updated_at)
            .where(StoryCluster.article_count == 1)
            .where(StoryCluster.first_seen_at != StoryCluster.last_updated_at)
        )
        rows = result.all()
        print(f"Found {len(rows)} singleton cluster(s) with drifted last_updated_at.")

        for cid, first_seen, last_updated in rows:
            print(f"  cluster {cid}: last_updated_at {last_updated} -> {first_seen}")
            if not dry_run:
                await session.execute(
                    update(StoryCluster).where(StoryCluster.id == cid).values(last_updated_at=first_seen)
                )

        if dry_run:
            print("Dry run — no rows changed.")
        else:
            await session.commit()
            print(f"Done. Reset last_updated_at on {len(rows)} cluster(s).")


if __name__ == "__main__":
    asyncio.run(main())
