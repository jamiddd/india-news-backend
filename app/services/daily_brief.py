"""Builds the Daily Brief for one morning: select yesterday's stories, have
Claude write the script, voice it with Sarvam, and store one DailyBrief row.

Run by scripts/run_daily_brief_scheduler.py at 05:00 IST and by the admin
"Regenerate" button. A brief with no audio (Sarvam/Supabase not configured, or
the voicing failed) is still published as text-only rather than not at all —
the summaries are useful on their own and the next regeneration can add audio.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import select, text

from app.database import AsyncSessionLocal
from app.models import DailyBrief
from app.redis_client import get_redis_client
from app.services import daily_brief_script, daily_brief_select, timeline_audio
from app.services.job_lease import job_lease

logger = logging.getLogger(__name__)

# The one place the served response's cache key lives, so a change to the
# response shape (bump the suffix) and every invalidation stay in step. See
# admin_timelines._invalidate_timeline_caches for what happens otherwise.
CACHE_KEY = "daily_brief:latest:v2"
LEASE_JOB = "daily_brief"
LEASE_TTL_SECONDS = 30 * 60
# Fewer than this many corroborated stories is not a brief worth publishing.
MIN_STORIES = 3

STATUS_TTL_SECONDS = 7 * 24 * 3600
RUNNING_TTL_SECONDS = 30 * 60


@dataclass
class BriefOutcome:
    ok: bool
    message: str
    has_audio: bool = False


def _status_key(brief_date: date) -> str:
    return f"daily_brief_status:{brief_date.isoformat()}"


async def set_status(brief_date: date, state: str, message: str = "") -> None:
    """Record "running" | "done" | "failed" for the admin page. Best-effort:
    Redis being down must never fail a build."""
    payload = json.dumps({"state": state, "message": message, "at": datetime.now(timezone.utc).isoformat()})
    ttl = RUNNING_TTL_SECONDS if state == "running" else STATUS_TTL_SECONDS
    try:
        await get_redis_client().setex(_status_key(brief_date), ttl, payload)
    except Exception as e:  # noqa: BLE001
        logger.warning("could not record daily brief status for %s: %s", brief_date, e)


async def get_status(brief_date: date) -> dict:
    try:
        value = await get_redis_client().get(_status_key(brief_date))
    except Exception as e:  # noqa: BLE001
        logger.warning("could not read daily brief status: %s", e)
        return {}
    return json.loads(value) if value else {}


async def in_progress() -> bool:
    """Whether a build currently holds the lease — the source of truth for
    "already running", shared by every worker and server."""
    try:
        async with AsyncSessionLocal() as s:
            row = await s.execute(
                text("SELECT 1 FROM job_lease WHERE job_name = :job AND expires_at > now()"),
                {"job": LEASE_JOB},
            )
            return row.first() is not None
    except Exception as e:  # noqa: BLE001
        logger.warning("could not check daily brief lease: %s", e)
        return False


async def run_build_task(brief_date: date, *, force: bool = True) -> None:
    """build_brief as a fire-and-forget task (the admin button): whatever
    happens, the status the page polls ends as done/failed, never stuck on
    "running"."""
    try:
        outcome = await build_brief(brief_date, force=force)
    except Exception as exc:  # noqa: BLE001
        logger.exception("daily brief %s crashed", brief_date)
        await set_status(brief_date, "failed", f"{type(exc).__name__}: {exc}")
        return
    if not outcome.ok:
        await set_status(brief_date, "failed", outcome.message)


async def invalidate_cache() -> None:
    try:
        await get_redis_client().delete(CACHE_KEY)
    except Exception:  # noqa: BLE001 - caching is an optimization, never a dependency
        pass


async def _upsert(brief_date: date, **fields) -> None:
    async with AsyncSessionLocal() as session:
        row = (await session.execute(select(DailyBrief).where(DailyBrief.brief_date == brief_date))).scalar_one_or_none()
        if row is None:
            row = DailyBrief(brief_date=brief_date)
            session.add(row)
        for key, value in fields.items():
            setattr(row, key, value)
        await session.commit()


async def build_brief(brief_date: date, *, force: bool = False) -> BriefOutcome:
    """Build (or with force, rebuild) the brief for brief_date. Never raises
    for an expected failure — returns a BriefOutcome; unexpected exceptions
    propagate to the caller, which reports them."""
    async with job_lease(LEASE_JOB, LEASE_TTL_SECONDS) as held:
        if not held:
            return BriefOutcome(False, "another brief build is already running")

        async with AsyncSessionLocal() as session:
            existing = (
                await session.execute(select(DailyBrief).where(DailyBrief.brief_date == brief_date))
            ).scalar_one_or_none()
            if existing is not None and existing.status == "ready" and not force:
                return BriefOutcome(True, "already built", has_audio=bool(existing.audio_url))
            had_ready = existing is not None and existing.status == "ready"

        await set_status(brief_date, "running", "selecting stories")
        # A regeneration must not take a live brief offline while it runs.
        if not had_ready:
            await _upsert(brief_date, status="building", error=None)

        async def fail(message: str) -> BriefOutcome:
            logger.warning("daily brief %s failed: %s", brief_date, message)
            if not had_ready:
                await _upsert(brief_date, status="failed", error=message)
            await set_status(brief_date, "failed", message)
            return BriefOutcome(False, message)

        async with AsyncSessionLocal() as session:
            stories = await daily_brief_select.select_stories(session, brief_date)
        if len(stories) < MIN_STORIES:
            return await fail(f"only {len(stories)} eligible stories")

        await set_status(brief_date, "running", "writing script")
        script = await daily_brief_script.write_script(stories)
        if script is None:
            return await fail("Claude did not return a valid script")

        audio = None
        if timeline_audio.is_configured():
            await set_status(brief_date, "running", "voicing")
            chunks, intro_chars = daily_brief_script.build_chunks(script)
            script_hash = timeline_audio.script_hash(script)
            audio = await timeline_audio.render_and_upload(
                f"daily brief {brief_date}", chunks, intro_chars,
                f"daily-brief/{brief_date.isoformat()}-{script_hash}.{timeline_audio.AUDIO_FILE_EXTENSION}",
            )
            if audio is None:
                logger.warning("daily brief %s: voicing failed, publishing text-only", brief_date)
        else:
            logger.warning("daily brief %s: audio not configured, publishing text-only", brief_date)

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
            status="ready",
            items=items,
            script=script,
            script_hash=timeline_audio.script_hash(script),
            audio_url=audio[0] if audio else None,
            audio_duration_seconds=audio[1] if audio else None,
            generated_at=datetime.now(timezone.utc),
            error=None if audio or not timeline_audio.is_configured() else "voicing failed; text-only",
        )
        await invalidate_cache()
        note = f"{len(stories)} stories, {'with audio' if audio else 'text-only'}"
        await set_status(brief_date, "done", note)
        return BriefOutcome(True, note, has_audio=audio is not None)
