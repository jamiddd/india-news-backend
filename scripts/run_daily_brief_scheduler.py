"""Daemon loop for the Daily Brief and the Late-Night Wrap-up — same
sleep-until-next-run shape as run_timeline_scheduler.py (no OS crontab; see
docker-compose.prod.yml's per-job scheduler services). Builds the Brief for
today's IST date at 05:00 IST (covering yesterday's stories) and the Wrap-up
at 19:30 IST (covering today's stories up to 7 PM — see
app.services.daily_brief_select.WRAPUP_CUTOFF).

Usage:
    python3 scripts/run_daily_brief_scheduler.py                      # daemon loop
    python3 scripts/run_daily_brief_scheduler.py --run-now            # build today's brief and exit
    python3 scripts/run_daily_brief_scheduler.py --run-now --kind wrapup
                                                                      # build today's wrap-up and exit
    python3 scripts/run_daily_brief_scheduler.py --run-now --date 2026-09-24 [--force] [--kind wrapup]
                                                                      # build a specific day's brief/wrap-up
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

IST = ZoneInfo("Asia/Kolkata")
RUN_TIMES = {"brief": time(5, 0), "wrapup": time(19, 30)}
PRIMARY_SCHEDULER_HOST = "newsapp"


def _assert_primary_host() -> None:
    """Single-instance, same opt-in as run_timeline_scheduler.py: two copies
    would double the Claude and Sarvam spend for one brief."""
    value = os.environ.get("PRIMARY_SCHEDULER_HOST")
    if value != PRIMARY_SCHEDULER_HOST:
        print(
            f"FATAL: PRIMARY_SCHEDULER_HOST is {value!r}, expected {PRIMARY_SCHEDULER_HOST!r}. "
            "Refusing to start — this scheduler is single-instance.",
            flush=True,
        )
        sys.exit(1)


async def run_once(brief_date: date, kind: str, *, force: bool = False) -> None:
    from app.services.admin_notify import notify_admin_failure
    from app.services.daily_brief import build_brief

    try:
        outcome = await build_brief(brief_date, kind, force=force)
    except Exception as exc:  # noqa: BLE001 - one failed cycle must not kill the daemon
        print(f"daily {kind} {brief_date} crashed: {exc}", flush=True)
        await notify_admin_failure("daily_brief_scheduler", exc)
        return
    print(f"daily {kind} {brief_date}: ok={outcome.ok} — {outcome.message}", flush=True)
    if not outcome.ok:
        await notify_admin_failure("daily_brief_scheduler", RuntimeError(outcome.message))


def _arg(name: str) -> str | None:
    if name in sys.argv:
        i = sys.argv.index(name)
        return sys.argv[i + 1] if i + 1 < len(sys.argv) else None
    return None


async def main() -> None:
    _assert_primary_host()

    if "--run-now" in sys.argv:
        raw = _arg("--date")
        brief_date = date.fromisoformat(raw) if raw else datetime.now(IST).date()
        kind = _arg("--kind") or "brief"
        await run_once(brief_date, kind, force="--force" in sys.argv)
        return

    # One action per pass (per kind), then sleep and re-evaluate. A restart
    # after a run time catches up that kind's build once (build_brief is a
    # no-op when it is already built), then waits for its next run time.
    while True:
        now = datetime.now(IST)
        next_runs = {}
        for kind, run_time in RUN_TIMES.items():
            today_run = datetime.combine(now.date(), run_time, tzinfo=IST)
            if today_run <= now:
                print(f"building daily {kind} for {now.date()}", flush=True)
                await run_once(now.date(), kind)
                next_runs[kind] = today_run + timedelta(days=1)
            else:
                next_runs[kind] = today_run
        next_run = min(next_runs.values())
        sleep_seconds = max(1, (next_run - datetime.now(IST)).total_seconds())
        print(f"next daily brief run at {next_run.isoformat()} ({sleep_seconds:.0f}s)", flush=True)
        await asyncio.sleep(sleep_seconds)


if __name__ == "__main__":
    asyncio.run(main())
