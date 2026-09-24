"""
Dry run: vertical (YouTube Shorts) vs. landscape (regular YouTube) video
ratio for articles published on a given IST day, or a range of days.

Read-only, same conventions as dry_run_video_count_yesterday.py (own
one-off NullPool engine built from the DATABASE_URL env var, IST day
boundaries converted to UTC for the published_at query).

Orientation is only known for YouTube videos: Article.video_is_short is
populated by _fetch_youtube_video_meta() (app/services/extractor.py) and is
NULL for every non-YouTube video (Brightcove, direct-stream) per the column
comment in app/models.py — there is no width/height/aspect-ratio field for
video the way image_width/image_height exists for images. Those rows are
reported separately as "orientation unknown" rather than guessed at.

Usage:
    python3 scripts/dry_run_video_orientation.py --date 2026-09-22
    python3 scripts/dry_run_video_orientation.py --start 2026-09-16 --end 2026-09-22
"""
import argparse
import asyncio
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")


def _asyncpg_url(raw: str) -> str:
    if raw.startswith("postgresql+asyncpg://"):
        return raw
    if raw.startswith("postgresql://"):
        return "postgresql+asyncpg://" + raw[len("postgresql://"):]
    if raw.startswith("postgres://"):
        return "postgresql+asyncpg://" + raw[len("postgres://"):]
    return raw


def _ist_day_bounds_utc(d: date):
    start_ist = datetime.combine(d, datetime.min.time(), tzinfo=IST)
    end_ist = start_ist + timedelta(days=1)
    return start_ist.astimezone(UTC), end_ist.astimezone(UTC)


QUERY = text("""
    SELECT
        COUNT(*) FILTER (WHERE video_url IS NOT NULL) AS with_video,
        COUNT(*) FILTER (WHERE video_is_short IS TRUE) AS vertical,
        COUNT(*) FILTER (WHERE video_is_short IS FALSE) AS landscape,
        COUNT(*) FILTER (WHERE video_url IS NOT NULL AND video_is_short IS NULL) AS unknown_orientation
    FROM articles
    WHERE published_at >= :start_utc AND published_at < :end_utc
""")


async def fetch_day(engine, d: date):
    start_utc, end_utc = _ist_day_bounds_utc(d)
    async with engine.connect() as conn:
        return (await conn.execute(QUERY, {"start_utc": start_utc, "end_utc": end_utc})).one()


async def main(days: list[date]):
    raw_url = os.environ.get("DATABASE_URL")
    if not raw_url:
        raise SystemExit("DATABASE_URL is not set in the environment")

    engine = create_async_engine(_asyncpg_url(raw_url), poolclass=NullPool, echo=False)

    totals = {"with_video": 0, "vertical": 0, "landscape": 0, "unknown_orientation": 0}
    try:
        for d in days:
            row = await fetch_day(engine, d)
            for k in totals:
                totals[k] += getattr(row, k)
            known = row.vertical + row.landscape
            ratio = f"{row.vertical}:{row.landscape}" if row.landscape else (f"{row.vertical}:0" if row.vertical else "n/a")
            print(f"{d.isoformat()}  video={row.with_video:<4} vertical={row.vertical:<4} "
                  f"landscape={row.landscape:<4} unknown={row.unknown_orientation:<4} "
                  f"ratio(v:l)={ratio}" + (f"  ({100*row.vertical/known:.0f}% vertical of known)" if known else ""))
    finally:
        await engine.dispose()

    known = totals["vertical"] + totals["landscape"]
    print()
    print(f"TOTAL over {len(days)} day(s):")
    print(f"  Videos:               {totals['with_video']}")
    print(f"  Vertical (Shorts):    {totals['vertical']}")
    print(f"  Landscape (regular):  {totals['landscape']}")
    print(f"  Orientation unknown:  {totals['unknown_orientation']}  (non-YouTube: Brightcove/direct-stream — no orientation data stored)")
    if known:
        print(f"  Ratio vertical:landscape = {totals['vertical']}:{totals['landscape']}"
              f"  ({100*totals['vertical']/known:.1f}% of orientation-known videos are vertical)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=lambda s: date.fromisoformat(s), default=None)
    parser.add_argument("--start", type=lambda s: date.fromisoformat(s), default=None)
    parser.add_argument("--end", type=lambda s: date.fromisoformat(s), default=None)
    args = parser.parse_args()

    if args.start and args.end:
        n = (args.end - args.start).days
        day_list = [args.start + timedelta(days=i) for i in range(n + 1)]
    elif args.date:
        day_list = [args.date]
    else:
        day_list = [datetime.now(IST).date() - timedelta(days=1)]

    asyncio.run(main(day_list))
