"""
One-off: refetch On This Day events (now with Wikimedia Commons images and
credits) for editorial rows stored before image support existed. Only
historical_events is touched — word, quote and background are left alone.
Rows where any event already has an image_url are skipped.

Usage (inside the app container):
    python3 scripts/backfill_history_images.py [--dry-run] [YYYY-MM-DD ...]

With no dates, every stored row is considered.
"""
import asyncio
import os
import sys
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import DailyEditorial
from app.services.editorial_features import _fetch_events


async def main(dates: list[date], dry_run: bool):
    async with AsyncSessionLocal() as session:
        query = select(DailyEditorial).order_by(DailyEditorial.feature_date)
        if dates:
            query = query.where(DailyEditorial.feature_date.in_(dates))
        rows = (await session.execute(query)).scalars().all()
        for row in rows:
            if any(e.get("image_url") for e in row.historical_events):
                print(f"{row.feature_date}: already has images, skipped")
                continue
            try:
                events = await _fetch_events(row.feature_date)
            except Exception as e:
                print(f"{row.feature_date}: fetch failed ({e}), left unchanged")
                continue
            with_image = sum(1 for e in events if e["image_url"])
            print(f"{row.feature_date}: {len(events)} events, {with_image} with image" + (" (dry run)" if dry_run else ""))
            if not dry_run:
                row.historical_events = events
                await session.commit()


if __name__ == "__main__":
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    dates = [date.fromisoformat(a) for a in args if a != "--dry-run"]
    asyncio.run(main(dates, dry_run))
