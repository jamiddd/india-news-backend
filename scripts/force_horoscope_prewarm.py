"""
One-off: fetch and store all twelve horoscope signs for today (or a given date)
right now, instead of waiting for the 06:00 IST top-up in the scheduler. Useful
after a night-long provider outage, or to confirm the provider has rolled over
to a date. Mutates the DB (inserts daily_horoscopes rows) — not read-only like
the other scripts/ here.

Partial results are normal: AstroJson rolls over on UTC, so a date that is
"today" in IST but still in the future for the provider will report 0/12.

Usage (inside the app or crossword_scheduler container):
    python3 scripts/force_horoscope_prewarm.py [YYYY-MM-DD]
"""
import asyncio
import os
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import AsyncSessionLocal
from app.services.horoscope import prewarm_horoscopes

IST = ZoneInfo("Asia/Kolkata")


async def main(target_date: date):
    ready, total = await prewarm_horoscopes(AsyncSessionLocal, target_date)
    print(f"{target_date}: {ready}/{total} signs stored")


if __name__ == "__main__":
    target_date = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else datetime.now(IST).date()
    asyncio.run(main(target_date))
