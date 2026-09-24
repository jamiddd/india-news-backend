"""
Dry run: counts how many articles had a video link for a given day.

Read-only — issues SELECTs only, nothing is written. Defaults to "yesterday"
in India time (Asia/Kolkata), 00:00 to 23:59:59.999999, matching the
day-boundary convention used elsewhere in this backend (see the IST/
INDIA_TZ zoneinfo usage in app/services/{crossword,daily_games,polls}.py).
articles.published_at is stored as a timezone-aware timestamptz, so the IST
window is converted to UTC before querying.

Builds its own one-off engine from the DATABASE_URL environment variable
(normalizing the scheme to postgresql+asyncpg://) rather than importing
app.config/app.database, so it never depends on a backend/.env file being
present and never touches backend config. NullPool, single connection,
closed at the end — see app/database.py's admin_engine() for why one-off
scripts should not borrow the app's pooled engine.

Usage:
    python3 scripts/dry_run_video_count_yesterday.py
    python3 scripts/dry_run_video_count_yesterday.py --date 2026-09-20
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


def _asyncpg_url(raw: str) -> str:
    if raw.startswith("postgresql+asyncpg://"):
        return raw
    if raw.startswith("postgresql://"):
        return "postgresql+asyncpg://" + raw[len("postgresql://"):]
    if raw.startswith("postgres://"):
        return "postgresql+asyncpg://" + raw[len("postgres://"):]
    return raw


async def main(target_date: date):
    raw_url = os.environ.get("DATABASE_URL")
    if not raw_url:
        raise SystemExit("DATABASE_URL is not set in the environment")

    engine = create_async_engine(_asyncpg_url(raw_url), poolclass=NullPool, echo=False)

    start_ist = datetime.combine(target_date, datetime.min.time(), tzinfo=IST)
    end_ist = start_ist + timedelta(days=1)  # exclusive upper bound
    start_utc = start_ist.astimezone(ZoneInfo("UTC"))
    end_utc = end_ist.astimezone(ZoneInfo("UTC"))

    query = text("""
        SELECT
            COUNT(*) AS total_articles,
            COUNT(video_url) AS with_video_url,
            COUNT(*) FILTER (WHERE media_type = 'video') AS media_type_video,
            COUNT(*) FILTER (
                WHERE video_url IS NOT NULL
                AND (video_url ILIKE '%youtube%' OR video_url ILIKE '%youtu.be%')
            ) AS youtube_video_url,
            COUNT(brightcove_video_id) AS brightcove_video_id_set,
            COUNT(DISTINCT source_id) FILTER (WHERE video_url IS NOT NULL) AS distinct_sources_with_video
        FROM articles
        WHERE published_at >= :start_utc AND published_at < :end_utc
    """)

    per_source_query = text("""
        SELECT s.name, COUNT(*) AS video_articles
        FROM articles a
        JOIN sources s ON s.id = a.source_id
        WHERE a.published_at >= :start_utc AND a.published_at < :end_utc
          AND a.video_url IS NOT NULL
        GROUP BY s.name
        ORDER BY video_articles DESC
    """)

    try:
        async with engine.connect() as conn:
            row = (await conn.execute(query, {"start_utc": start_utc, "end_utc": end_utc})).one()
            per_source = (await conn.execute(per_source_query, {"start_utc": start_utc, "end_utc": end_utc})).all()
    finally:
        await engine.dispose()

    total = row.total_articles
    with_video = row.with_video_url
    pct = (100.0 * with_video / total) if total else 0.0

    print(f"Window: {target_date.isoformat()} 00:00–23:59:59 IST "
          f"({start_utc.isoformat()} to {end_utc.isoformat()} UTC)")
    print()
    print(f"Total articles published:      {total}")
    print(f"Articles with video_url set:   {with_video} ({pct:.1f}%)")
    print(f"  - media_type = 'video':       {row.media_type_video}")
    print(f"  - youtube links:              {row.youtube_video_url}")
    print(f"  - brightcove_video_id set:    {row.brightcove_video_id_set}")
    print(f"Distinct sources with a video:  {row.distinct_sources_with_video}")

    if per_source:
        print()
        print("By source:")
        for r in per_source:
            print(f"  {r.name:<40} {r.video_articles}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--date", type=lambda s: date.fromisoformat(s), default=None,
        help="IST calendar date to check, YYYY-MM-DD (default: yesterday)",
    )
    args = parser.parse_args()
    target = args.date or (datetime.now(IST).date() - timedelta(days=1))
    asyncio.run(main(target))
