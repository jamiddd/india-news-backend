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
from app.services.admin_notify import notify_admin_reviews_ready
from app.services.polls import IST, activate_poll, generate_draft, seed_fallbacks


async def prepare(day):
    async with AsyncSessionLocal() as session:
        try:
            already_existed = await session.scalar(select(DailyPoll).where(DailyPoll.poll_date == day)) is not None
            poll = await generate_draft(session, day)
            print(f"Poll draft ready for {day}", flush=True)
            if not already_existed:
                # One push for both reviews. The quiz draft for today was
                # written at 23:55 last night, so it is already here to
                # report on. See app/services/admin_notify.py.
                sent = await notify_admin_reviews_ready(session, day)
                print(f"Admin review push {'sent' if sent else 'not sent'} for {day}", flush=True)
        except Exception as exc:
            print(f"Poll draft failed for {day}: {exc}", flush=True)


async def notify_test():
    """Fire the real review push for today, reporting today's real state.

    Deliberately not a synthetic message: the thing worth testing is whether
    the push reflects what is actually outstanding, and a stand-in poll would
    test only FCM delivery.
    """
    async with AsyncSessionLocal() as session:
        today = datetime.now(IST).date()
        from app.admin_home import pending_reviews
        from app.services.admin_notify import compose
        tasks = await pending_reviews(session, today)
        composed = compose(tasks)
        if composed is None:
            print(f"Nothing waiting for {today} — no push would be sent.", flush=True)
            print(f"State: {[(k, v['status']) for k, v in tasks.items()]}", flush=True)
            return
        sent = await notify_admin_reviews_ready(session, today)
        print(f"{'Sent' if sent else 'Composed but not sent'}: {composed[0]!r} / {composed[1]!r}", flush=True)


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
