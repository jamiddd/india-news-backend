"""Prepare the AI poll draft at 04:30 and publish at 09:00 Asia/Kolkata."""
import asyncio
from datetime import datetime, time, timedelta

from sqlalchemy import text

from app.database import AsyncSessionLocal, Base, engine
from app.services.polls import IST, activate_poll, generate_draft, seed_fallbacks


async def prepare(day):
    async with AsyncSessionLocal() as session:
        try:
            await generate_draft(session, day)
            print(f"Poll draft ready for {day}", flush=True)
        except Exception as exc:
            print(f"Poll draft failed for {day}: {exc}", flush=True)


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
    while True:
        now = datetime.now(IST)
        draft_at = datetime.combine(now.date(), time(4, 30), tzinfo=IST)
        publish_at = datetime.combine(now.date(), time(9), tzinfo=IST)
        if now >= publish_at:
            await publish(now.date())
            next_run, action = datetime.combine(now.date() + timedelta(days=1), time(4, 30), tzinfo=IST), "draft"
        elif now >= draft_at:
            await prepare(now.date())
            next_run, action = publish_at, "publish"
        else:
            next_run, action = draft_at, "draft"
        await asyncio.sleep(max(1, (next_run - datetime.now(IST)).total_seconds()))
        if action == "draft": await prepare(next_run.date())
        else: await publish(next_run.date())


if __name__ == "__main__": asyncio.run(main())
