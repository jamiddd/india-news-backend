"""Prepare the AI poll draft at 04:30 and publish at 09:00 Asia/Kolkata.

Usage:
    python3 scripts/run_poll_scheduler.py               # normal daemon loop
    python3 scripts/run_poll_scheduler.py --notify-test  # fire the admin FCM
                                                          # push right now and
                                                          # exit, using today's
                                                          # draft/active poll
                                                          # if one exists or a
                                                          # throwaway stand-in
                                                          # question otherwise
"""
import asyncio
import sys
from datetime import datetime, time, timedelta

from sqlalchemy import select, text

from app.database import AsyncSessionLocal, Base, engine
from app.models import DailyPoll
from app.services.polls import IST, activate_poll, generate_draft, notify_admin_draft_ready, seed_fallbacks


async def prepare(day):
    async with AsyncSessionLocal() as session:
        try:
            already_existed = await session.scalar(select(DailyPoll).where(DailyPoll.poll_date == day)) is not None
            poll = await generate_draft(session, day)
            print(f"Poll draft ready for {day}", flush=True)
            if not already_existed:
                await notify_admin_draft_ready(session, poll)
        except Exception as exc:
            print(f"Poll draft failed for {day}: {exc}", flush=True)


async def notify_test():
    async with AsyncSessionLocal() as session:
        today = datetime.now(IST).date()
        poll = await session.scalar(select(DailyPoll).where(DailyPoll.poll_date == today))
        if poll is None:
            # Not persisted — notify_admin_draft_ready only reads .question,
            # so a throwaway in-memory stand-in is enough to test delivery
            # without touching real poll data.
            poll = DailyPoll(question="[Test] Is this notification working?")
        await notify_admin_draft_ready(session, poll)
        print(f"Sent test notification (poll question: {poll.question!r})", flush=True)


async def publish(day):
    async with AsyncSessionLocal() as session:
        poll = await activate_poll(session, day)
        print(f"Poll {poll.id} active for {day} ({poll.generation_method})", flush=True)


async def main():
    async with engine.begin() as connection:
        await connection.execute(text("SELECT pg_advisory_lock(918273645)"))
        try: await connection.run_sync(Base.metadata.create_all)
        finally: await connection.execute(text("SELECT pg_advisory_unlock(918273645)"))
    async with AsyncSessionLocal() as session: await seed_fallbacks(session)
    # One action per pass, then sleep and re-evaluate. The loop used to do
    # two — a catch-up for the current window, then a second action after the
    # sleep — which meant the next pass repeated whatever the sleep had just
    # done. Every draft and every publish ran twice (visible as doubled lines
    # in docker logs), and on a day where drafting failed that was two full
    # sets of Claude retries instead of one.
    while True:
        now = datetime.now(IST)
        draft_at = datetime.combine(now.date(), time(4, 30), tzinfo=IST)
        publish_at = datetime.combine(now.date(), time(9), tzinfo=IST)
        if now >= publish_at:
            await publish(now.date())
            next_run = datetime.combine(now.date() + timedelta(days=1), time(4, 30), tzinfo=IST)
        elif now >= draft_at:
            await prepare(now.date())
            next_run = publish_at
        else:
            next_run = draft_at
        await asyncio.sleep(max(1, (next_run - datetime.now(IST)).total_seconds()))


if __name__ == "__main__":
    asyncio.run(notify_test() if "--notify-test" in sys.argv else main())
