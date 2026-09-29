"""Builds a Daily Brief row: select the window's stories, have Claude write
the script, voice it with Sarvam, and store one DailyBrief row. Two kinds
share this pipeline, distinguished only by `kind` and each other's own
(brief_date, kind) row: 'brief' (yesterday's stories, built 05:00 IST) and
'wrapup' (today's stories up to 7 PM IST, the Late-Night Wrap-up, built
19:30 IST) — see daily_brief_select.brief_window and daily_brief_script's
per-kind wording.

Run by scripts/run_daily_brief_scheduler.py and by the admin "Regenerate"
button (app/admin_daily_brief.py). A brief with no audio (Sarvam/Supabase not
configured, or the voicing failed) is still published as text-only rather
than not at all — the summaries are useful on their own and the next
regeneration can add audio.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import select, text

from app.database import AsyncSessionLocal
from app.models import DailyBrief
from app.redis_client import get_redis_client
from app.services import daily_brief_script, daily_brief_select, timeline_audio
from app.services.job_lease import job_lease

logger = logging.getLogger(__name__)

# The one place the served response's cache keys live, so a change to the
# response shape (bump the suffix) and every invalidation stay in step. See
# admin_timelines._invalidate_timeline_caches for what happens otherwise. One
# key per kind, so building/invalidating one never touches the other's cache.
CACHE_KEYS = {"brief": "daily_brief:latest:v3:brief", "wrapup": "daily_brief:latest:v3:wrapup"}
LEASE_TTL_SECONDS = 30 * 60
# Fewer than this many corroborated stories is not a brief worth publishing.
MIN_STORIES = 3

STATUS_TTL_SECONDS = 7 * 24 * 3600
RUNNING_TTL_SECONDS = 30 * 60

IST = ZoneInfo("Asia/Kolkata")
# The Late-Night Wrap-up's on-screen window (see main.py's get_daily_brief):
# from NIGHT_START through midnight to NIGHT_END the next morning, the app
# prefers the Wrap-up over the Brief.
NIGHT_START = time(20, 0)
NIGHT_END = time(5, 0)


def served_kind(now_ist: Optional[datetime] = None) -> str:
    """Which kind GET /daily-brief should prefer right now: 'wrapup' from
    NIGHT_START to NIGHT_END IST, 'brief' the rest of the day. The caller
    falls back to the other kind when the preferred one has no ready row."""
    now = (now_ist or datetime.now(IST)).astimezone(IST)
    t = now.time()
    return "wrapup" if t >= NIGHT_START or t < NIGHT_END else "brief"


def _lease_job(kind: str) -> str:
    # Kept as the bare "daily_brief" for the morning kind so an in-flight
    # lease from before this column existed still matches.
    return "daily_brief" if kind == "brief" else f"daily_brief_{kind}"


@dataclass
class BriefOutcome:
    ok: bool
    message: str
    has_audio: bool = False


def _status_key(brief_date: date, kind: str) -> str:
    return f"daily_brief_status:{brief_date.isoformat()}:{kind}"


async def set_status(brief_date: date, kind: str, state: str, message: str = "") -> None:
    """Record "running" | "done" | "failed" for the admin page. Best-effort:
    Redis being down must never fail a build."""
    payload = json.dumps({"state": state, "message": message, "at": datetime.now(timezone.utc).isoformat()})
    ttl = RUNNING_TTL_SECONDS if state == "running" else STATUS_TTL_SECONDS
    try:
        await get_redis_client().setex(_status_key(brief_date, kind), ttl, payload)
    except Exception as e:  # noqa: BLE001
        logger.warning("could not record daily brief status for %s/%s: %s", brief_date, kind, e)


async def get_status(brief_date: date, kind: str = "brief") -> dict:
    try:
        value = await get_redis_client().get(_status_key(brief_date, kind))
    except Exception as e:  # noqa: BLE001
        logger.warning("could not read daily brief status: %s", e)
        return {}
    return json.loads(value) if value else {}


async def in_progress(kind: str = "brief") -> bool:
    """Whether a build of this kind currently holds the lease — the source of
    truth for "already running", shared by every worker and server."""
    try:
        async with AsyncSessionLocal() as s:
            row = await s.execute(
                text("SELECT 1 FROM job_lease WHERE job_name = :job AND expires_at > now()"),
                {"job": _lease_job(kind)},
            )
            return row.first() is not None
    except Exception as e:  # noqa: BLE001
        logger.warning("could not check daily brief lease: %s", e)
        return False


async def run_build_task(brief_date: date, kind: str = "brief", *, force: bool = True) -> None:
    """build_brief as a fire-and-forget task (the admin button): whatever
    happens, the status the page polls ends as done/failed, never stuck on
    "running"."""
    try:
        outcome = await build_brief(brief_date, kind, force=force)
    except Exception as exc:  # noqa: BLE001
        logger.exception("daily brief %s/%s crashed", brief_date, kind)
        await set_status(brief_date, kind, "failed", f"{type(exc).__name__}: {exc}")
        return
    if not outcome.ok:
        await set_status(brief_date, kind, "failed", outcome.message)


async def invalidate_cache(kind: str = "brief") -> None:
    try:
        await get_redis_client().delete(CACHE_KEYS[kind])
    except Exception:  # noqa: BLE001 - caching is an optimization, never a dependency
        pass


async def _upsert(brief_date: date, kind: str, **fields) -> None:
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(DailyBrief).where(DailyBrief.brief_date == brief_date, DailyBrief.kind == kind)
            )
        ).scalar_one_or_none()
        if row is None:
            row = DailyBrief(brief_date=brief_date, kind=kind)
            session.add(row)
        for key, value in fields.items():
            setattr(row, key, value)
        await session.commit()


async def build_brief(brief_date: date, kind: str = "brief", *, force: bool = False) -> BriefOutcome:
    """Build (or with force, rebuild) the brief for (brief_date, kind). kind
    is 'brief' (the morning Daily Brief, yesterday's stories) or 'wrapup'
    (the Late-Night Wrap-up, today's stories up to 7 PM). Never raises for an
    expected failure — returns a BriefOutcome; unexpected exceptions
    propagate to the caller, which reports them."""
    async with job_lease(_lease_job(kind), LEASE_TTL_SECONDS) as held:
        if not held:
            return BriefOutcome(False, f"another {kind} build is already running")

        async with AsyncSessionLocal() as session:
            existing = (
                await session.execute(
                    select(DailyBrief).where(DailyBrief.brief_date == brief_date, DailyBrief.kind == kind)
                )
            ).scalar_one_or_none()
            if existing is not None and existing.status == "ready" and not force:
                return BriefOutcome(True, "already built", has_audio=bool(existing.audio_url))
            had_ready = existing is not None and existing.status == "ready"

        await set_status(brief_date, kind, "running", "selecting stories")
        # A regeneration must not take a live brief offline while it runs.
        if not had_ready:
            await _upsert(brief_date, kind, status="building", error=None)

        async def fail(message: str) -> BriefOutcome:
            logger.warning("daily brief %s/%s failed: %s", brief_date, kind, message)
            if not had_ready:
                await _upsert(brief_date, kind, status="failed", error=message)
            await set_status(brief_date, kind, "failed", message)
            return BriefOutcome(False, message)

        async with AsyncSessionLocal() as session:
            stories = await daily_brief_select.select_stories(session, brief_date, kind)
        if len(stories) < MIN_STORIES:
            return await fail(f"only {len(stories)} eligible stories")

        await set_status(brief_date, kind, "running", "writing script")
        script = await daily_brief_script.write_script(stories, kind)
        if script is None:
            return await fail("Claude did not return a valid script")

        audio = None
        if timeline_audio.is_configured():
            await set_status(brief_date, kind, "running", "voicing")
            chunks, intro_chars = daily_brief_script.build_chunks(script, kind)
            script_hash = timeline_audio.script_hash(script)
            audio = await timeline_audio.render_and_upload(
                f"daily {kind} {brief_date}", chunks, intro_chars,
                f"daily-brief/{brief_date.isoformat()}-{kind}-{script_hash}.{timeline_audio.AUDIO_FILE_EXTENSION}",
            )
            if audio is None:
                logger.warning("daily brief %s/%s: voicing failed, publishing text-only", brief_date, kind)
        else:
            logger.warning("daily brief %s/%s: audio not configured, publishing text-only", brief_date, kind)

        offsets = audio[2] if audio else [None] * len(stories)
        summaries = {i["cluster_id"]: i["summary"] for i in script["items"]}
        items = [
            {
                "cluster_id": s.cluster_id,
                "headline": s.headline,
                "summary": summaries[s.cluster_id],
                "category": s.category,
                "source_count": s.source_count,
                "image_url": s.image_url,
                "slot_kind": s.slot_kind,
                "audio_offset": offsets[i],
            }
            for i, s in enumerate(stories)
        ]
        await _upsert(
            brief_date,
            kind,
            status="ready",
            items=items,
            script=script,
            script_hash=timeline_audio.script_hash(script),
            audio_url=audio[0] if audio else None,
            audio_duration_seconds=audio[1] if audio else None,
            generated_at=datetime.now(timezone.utc),
            error=None if audio or not timeline_audio.is_configured() else "voicing failed; text-only",
        )
        await invalidate_cache(kind)
        note = f"{len(stories)} stories, {'with audio' if audio else 'text-only'}"
        await set_status(brief_date, kind, "done", note)
        return BriefOutcome(True, note, has_audio=audio is not None)
