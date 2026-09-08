"""Daemon loop for the Timeline/Context tab's generation cycle — same
sleep-until-next-run shape as run_poll_scheduler.py/run_crossword_scheduler.py,
not OS crontab (this repo doesn't use one; see docker-compose.prod.yml's
per-job scheduler services). Runs scripts/build_story_timelines.run() once a
day at a fixed Asia/Kolkata time.

Was 3x/day (08:00/14:00/20:00 IST) until 2026-09-08: once the picks
themselves are curated (see app/admin_timelines.py), a story holding steady
for a full day reads as a stable "story so far", not staleness — refreshing
it mid-day just to re-run a chain that hasn't moved was the cost, not the
benefit, of the higher cadence.

Usage:
    python3 scripts/run_timeline_scheduler.py               # normal daemon loop
    python3 scripts/run_timeline_scheduler.py --run-now      # run one cycle
                                                              # immediately
                                                              # and exit,
                                                              # instead of
                                                              # waiting for
                                                              # the next
                                                              # scheduled time
"""
import asyncio
import os
import sys
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts.build_story_timelines import run as run_generation  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")

# 06:00 IST — once a day, before the morning news cycle starts, so readers
# get a fresh "story so far" first thing and it holds steady the rest of the
# day rather than shifting under them 2-3 times.
RUN_TIMES = [time(6, 0)]


async def run_once() -> None:
    try:
        await run_generation()
    except Exception as exc:
        # Best-effort, same posture as run_poll_scheduler.py's prepare():
        # one failed cycle (a transient LLM/DB error) shouldn't kill the
        # daemon — the next scheduled run tries again a few hours later,
        # and nothing about story_timeline_features rows gets corrupted by
        # a run that dies partway (every write happens inside run()'s own
        # session.commit(), so a mid-run exception just means this cycle's
        # remaining candidates never got attempted).
        print(f"timeline generation cycle failed: {exc}", flush=True)


async def main() -> None:
    if "--run-now" in sys.argv:
        await run_once()
        return

    # One action per pass, then sleep and re-evaluate — see
    # run_poll_scheduler.py's main() for why a "catch-up, then act again
    # after sleeping" shape double-runs the action it just caught up on.
    # A restart mid-day still gets a same-pass catch-up run for whichever
    # boundary already passed today (at most one, this pass), then
    # proceeds strictly forward from there.
    while True:
        now = datetime.now(IST)
        run_dts = sorted(datetime.combine(now.date(), t, tzinfo=IST) for t in RUN_TIMES)
        passed = [dt for dt in run_dts if dt <= now]
        if passed:
            latest_passed = max(passed)
            print(f"running timeline generation cycle for {latest_passed.isoformat()} slot", flush=True)
            await run_once()
            upcoming = [dt for dt in run_dts if dt > latest_passed]
            next_run = upcoming[0] if upcoming else run_dts[0] + timedelta(days=1)
        else:
            next_run = run_dts[0]

        sleep_seconds = max(1, (next_run - datetime.now(IST)).total_seconds())
        print(f"next timeline generation cycle at {next_run.isoformat()} ({sleep_seconds:.0f}s)", flush=True)
        await asyncio.sleep(sleep_seconds)


if __name__ == "__main__":
    asyncio.run(main())
