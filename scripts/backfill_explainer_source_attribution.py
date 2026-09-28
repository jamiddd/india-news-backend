"""
One-off backfill: recompute Explainer.sources for rows generated before
cluster_to_source() started returning source_count/outlet_names/outlet_urls
(2026-09-28) — the field the app uses to show "Outlet A, Outlet B and N
others" + stacked favicons instead of implying a source deep-links to a
single publisher's own article (it deep-links to the full aggregated story).

Deliberately NOT a "Regenerate all" — same reasoning as
backfill_explainer_hero_images.py: that re-runs the full Claude answer
generation (and narration, if requested), which costs money and risks the
published quick_answer/sections drifting from what was reviewed and
published. This only recomputes the one JSON field, the same way
build_explainer() does, from the explainer's existing source_cluster_ids —
no Claude call, no narration call.

Safe to re-run any time (idempotent: every row with source_cluster_ids is
recomputed fresh each run, so a row already backfilled just gets the same
answer again — cheap, no Claude/narration cost, just a DB read).

Usage:
    python3 scripts/backfill_explainer_source_attribution.py [--dry-run]
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import Explainer
from app.services.explainer import cluster_to_source, fetch_source_clusters, invalidate_detail_cache, invalidate_list_cache

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main():
    dry_run = "--dry-run" in sys.argv
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(Explainer).where(Explainer.source_cluster_ids.isnot(None))
            )
        ).scalars().all()
        logger.info(f"Found {len(rows)} explainer(s) with source_cluster_ids.")

        updated_ids: list[int] = []
        for row in rows:
            source_ids = list(row.source_cluster_ids or [])
            if not source_ids or not row.sources:
                continue
            clusters = await fetch_source_clusters(source_ids)
            if not clusters:
                logger.info(f"  id={row.id}: none of its source clusters still exist, skipping")
                continue
            new_sources = [cluster_to_source(c) for c in clusters]
            logger.info(f"  id={row.id}: {[s['source_count'] for s in new_sources]} outlets per source")
            if not dry_run:
                row.sources = new_sources
                updated_ids.append(row.id)

        if dry_run:
            logger.info("Dry run — no rows changed.")
        else:
            await session.commit()
            for explainer_id in updated_ids:
                # A published explainer's own GET /explainers/{id} response
                # is cached separately from the feed list — invalidate_list_cache()
                # alone leaves the stale pre-backfill sources serving until
                # CACHE_TTL_SECONDS expires (the bug that motivated adding
                # invalidate_detail_cache() at all, caught running this script).
                await invalidate_detail_cache(explainer_id)
            if updated_ids:
                await invalidate_list_cache()
            logger.info(f"Done. Backfilled sources on {len(updated_ids)} explainer row(s).")


if __name__ == "__main__":
    asyncio.run(main())
