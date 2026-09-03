"""
One-off: replace an already-published daily poll with a freshly AI-drafted one.

generate_poll_now.py cannot do this. generate_draft() refuses any poll whose
status is not "draft" (HTTP 409), which is exactly the situation this script
exists for: the 04:30 IST draft failed, activate_poll auto-published a
generic entry from the fallback bank at 09:00, and the day is now stuck with
a fallback poll that nothing can overwrite.

So this demotes the row back to "draft", regenerates it through the same
generate_draft() the scheduler uses, and re-activates it — publish_at has
long since passed by the time anyone runs this, so approve_poll would 409
too, and the status is set directly instead.

DESTRUCTIVE: replacing the question means replacing its options, and
poll_votes.option_id is ON DELETE CASCADE — every vote already cast on the
poll is deleted with them. Keeping them would be worse: the counts would
be carried over onto a different question. The script refuses to run when
votes exist unless --force is passed, and always prints the count first.

Usage (inside the app container):
    python3 scripts/force_poll_regen.py                # today (IST), refuse if votes exist
    python3 scripts/force_poll_regen.py --force        # today, discard existing votes
    python3 scripts/force_poll_regen.py 2026-09-03 --force
    python3 scripts/force_poll_regen.py --dry-run      # show what would be replaced, change nothing
"""
import asyncio
import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import func, select

from app.database import AsyncSessionLocal
from app.models import DailyPoll, PollOption, PollVote
from app.services.polls import IST, generate_draft


async def main(target: date, force: bool, dry_run: bool) -> int:
    async with AsyncSessionLocal() as session:
        poll = await session.scalar(select(DailyPoll).where(DailyPoll.poll_date == target))
        if not poll:
            print(f"No poll row for {target} — nothing to replace. Use generate_poll_now.py instead.")
            return 1

        votes = await session.scalar(select(func.count(PollVote.id)).where(PollVote.poll_id == poll.id)) or 0
        options = (await session.execute(
            select(PollOption).where(PollOption.poll_id == poll.id).order_by(PollOption.position)
        )).scalars().all()

        print(f"Current poll for {target} (id={poll.id}, status={poll.status}, method={poll.generation_method})")
        print(f"  Question: {poll.question}")
        for option in options:
            print(f"    - {option.text}")
        print(f"  Votes cast: {votes}")

        if dry_run:
            print("\n--dry-run: nothing changed.")
            return 0
        if votes and not force:
            print(f"\nRefusing: {votes} vote(s) would be deleted with the current options. Re-run with --force.")
            return 1

        # generate_draft only touches a "draft" row, and only re-activates via
        # approve_poll (which refuses once publish_at has passed) — so bracket
        # it with the status changes it won't make itself.
        previous_status = poll.status
        poll.status = "draft"
        await session.commit()
        try:
            poll = await generate_draft(session, target, replace=True)
        except Exception:
            poll.status = previous_status
            await session.commit()
            raise
        poll.status = "active"
        await session.commit()
        await session.refresh(poll)

        options = (await session.execute(
            select(PollOption).where(PollOption.poll_id == poll.id).order_by(PollOption.position)
        )).scalars().all()
        print(f"\nReplaced. Now active (method={poll.generation_method})")
        print(f"  Question: {poll.question}")
        print(f"  Context: {poll.context}")
        for option in options:
            print(f"    - {option.text}")
        print(f"  Source headline: {poll.source_headline}")
        return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    target_date = date.fromisoformat(args[0]) if args else datetime.now(IST).date()
    sys.exit(asyncio.run(main(target_date, "--force" in sys.argv, "--dry-run" in sys.argv)))
