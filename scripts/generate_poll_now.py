"""One-off manual trigger to verify the AI poll-drafting path end to end,
independent of the 04:30 IST scheduler. Calls the exact same generate_draft()
the scheduler uses, so a printed real question/options tied to a source
headline confirms Claude actually drafted it (vs. the fallback bank, which
this path never touches). Persists a normal `draft` row, reviewable/
rejectable at /admin/polls same as any scheduler-produced draft.

Usage:
    python3 scripts/generate_poll_now.py            # today's date, reuse existing draft if any
    python3 scripts/generate_poll_now.py --replace  # force a fresh draft even if one exists
"""
import asyncio
import os
import sys
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import AsyncSessionLocal
from app.services.polls import generate_draft


async def main():
    replace = "--replace" in sys.argv
    async with AsyncSessionLocal() as session:
        poll = await generate_draft(session, date.today(), replace=replace)
        print(f"Question: {poll.question}")
        print(f"Context: {poll.context}")
        print(f"Source headline: {poll.source_headline}")
        print(f"Generation method: {poll.generation_method}")


if __name__ == "__main__":
    asyncio.run(main())
