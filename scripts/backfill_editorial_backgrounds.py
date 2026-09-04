"""
One-off backfill: point every daily_editorial_features row at a background
image in our own Supabase Storage bucket.

Rows written before the Unsplash removal either hold a dead Unsplash CDN url
or nothing at all (a fetch that failed at creation time cached `null`
permanently). `_ensure_background` in editorial_features.py already heals a
row when that date is requested, but only then — dates nobody browses back to
keep their stale value indefinitely. This does the same assignment for every
row in one pass, using the identical `toordinal() % len(names)` rule, so the
result is exactly what lazy healing would eventually produce.

Not required for correctness — the API is already serving correct urls for
any date that gets read. This is for tidiness, and so a future change to the
healing logic doesn't have to reason about legacy shapes still in the table.

Idempotent: rows already pointing at the current bucket are skipped, so it is
safe to re-run (e.g. after uploading a different set of images, where the
rotation shifts because the filename list changed length).

Run on ONE droplet only — both share the same Postgres.

Usage:
    python3 scripts/backfill_editorial_backgrounds.py [--dry-run]
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import DailyEditorial
from app.services.editorial_backgrounds import background_object_names, public_url

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main():
    dry_run = "--dry-run" in sys.argv

    names = await background_object_names()
    if not names:
        # Without a listing every row would be assigned `null`, which is
        # strictly worse than the stale urls already there.
        logger.error(
            "Bucket listing is empty or unavailable — refusing to run. Check "
            "SUPABASE_URL / SUPABASE_SERVICE_KEY and that the bucket is public "
            "and populated."
        )
        return 1
    logger.info(f"{len(names)} images in the bucket — rotation length {len(names)}.")

    current_prefix = public_url("")

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(DailyEditorial).order_by(DailyEditorial.feature_date)
        )
        rows = result.scalars().all()
        logger.info(f"{len(rows)} daily_editorial_features rows.")

        already_current = 0
        updated = 0
        for row in rows:
            existing = (row.background_image or {}).get("url") or ""
            if existing.startswith(current_prefix):
                already_current += 1
                continue

            name = names[row.feature_date.toordinal() % len(names)]
            was = "null" if not existing else existing
            logger.info(f"  {row.feature_date}: {was} -> {name}")
            if not dry_run:
                row.background_image = {"url": public_url(name)}
            updated += 1

        logger.info(
            f"{already_current} already current, {updated} to update."
        )
        if dry_run:
            logger.info("Dry run — no rows changed.")
        else:
            await session.commit()
            logger.info(f"Done. Updated {updated} rows.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
