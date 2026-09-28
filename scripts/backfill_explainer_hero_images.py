"""
One-off backfill: populate Explainer.hero_image_url for rows generated before
explainer.py's derive_hero_image() existed (2026-09-28).

Deliberately NOT a "Regenerate all" — that re-runs the full Claude answer
generation (and narration, if requested), which costs money and risks the
published quick_answer/sections drifting from what was reviewed and
published. This only recomputes the one new field, the same way
build_explainer() does, from the explainer's existing source_cluster_ids —
no Claude call, no narration call.

Safe to re-run any time (idempotent: only rows with hero_image_url IS NULL
are touched; a row whose sources still have no image is left null and just
gets tried again next run in case a source cluster's image was added since).

Usage:
    python3 scripts/backfill_explainer_hero_images.py [--dry-run]
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import Explainer
from app.services.explainer import derive_hero_image, fetch_source_clusters, invalidate_detail_cache, invalidate_list_cache

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main():
    dry_run = "--dry-run" in sys.argv
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(Explainer).where(Explainer.hero_image_url.is_(None))
            )
        ).scalars().all()
        logger.info(f"Found {len(rows)} explainer(s) with no hero_image_url.")

        updated_ids: list[int] = []
        for row in rows:
            source_ids = list(row.source_cluster_ids or [])
            if not source_ids:
                logger.info(f"  id={row.id}: no source_cluster_ids, skipping")
                continue
            clusters = await fetch_source_clusters(source_ids)
            image_url = derive_hero_image(clusters)
            if image_url is None:
                logger.info(f"  id={row.id}: none of its {len(clusters)} sources have an image")
                continue
            logger.info(f"  id={row.id}: {image_url}")
            if not dry_run:
                row.hero_image_url = image_url
                updated_ids.append(row.id)

        if dry_run:
            logger.info("Dry run — no rows changed.")
        else:
            await session.commit()
            for explainer_id in updated_ids:
                # A published explainer's own GET /explainers/{id} response
                # is cached separately from the feed list — invalidate_list_cache()
                # alone can leave the stale pre-backfill hero_image_url
                # serving until CACHE_TTL_SECONDS expires.
                await invalidate_detail_cache(explainer_id)
            if updated_ids:
                await invalidate_list_cache()
            logger.info(f"Done. Backfilled hero_image_url on {len(updated_ids)} explainer row(s).")


if __name__ == "__main__":
    asyncio.run(main())
