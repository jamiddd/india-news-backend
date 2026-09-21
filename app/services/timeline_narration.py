"""Narrate ONE timeline row on demand: Claude writes the spoken script, Sarvam
voices it, and the result is stored on the row.

Shared by the admin "Generate narration" button (app/admin_timelines.py) and
scripts/backfill_timeline_audio.py, so both do exactly the same thing. The
nightly narrator (scripts/build_story_timelines.py) narrates chains as it
regenerates them on its own; this is the manual, per-row path for rolling the
current voice out to existing timelines, or redoing one, whenever you choose.

A run takes a few minutes (a Claude call, then about one Sarvam call per beat)
and costs roughly Rs 15-20 for a typical story, so it never holds a database
session open while it waits, and it is guarded three ways:
  - a per-row lease (job_lease) so a double click, or two servers, cannot run
    the same row twice at once;
  - the row is left completely untouched unless the whole run succeeds — the
    existing audio keeps playing if anything fails;
  - the row's written context/beats are compared before saving, so a run that
    overlapped a narrative regeneration (whose beats the new audio would no
    longer line up with) is rejected instead of saved.
The outcome of the last run is kept in Redis (best-effort, shared by all
servers) so the admin page can show "running / done / failed" for a run that
was started on another worker.
"""
from __future__ import annotations

import copy
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional

from sqlalchemy import text

from app.database import AsyncSessionLocal
from app.models import StoryTimelineFeature
from app.redis_client import get_redis_client
from app.services.job_lease import job_lease
from app.services.timeline_audio import generate_audio, is_configured
from app.services.timeline_narrative import TimelineNarrativeError, call_claude_spoken_script_only

logger = logging.getLogger(__name__)

# The lease outlives a normal run only through its heartbeat, so a crashed run
# frees the row after about this long.
LEASE_TTL_SECONDS = 180
# A finished run's outcome stays visible for a week; a "running" marker that a
# crash never cleared expires on its own after half an hour.
STATUS_TTL_SECONDS = 7 * 24 * 3600
RUNNING_TTL_SECONDS = 30 * 60


@dataclass
class NarrationOutcome:
    ok: bool
    message: str


def lease_name(row_id: int) -> str:
    return f"timeline-narrate:{row_id}"


def _status_key(row_id: int) -> str:
    return f"timeline_narration:{row_id}"


def can_narrate(row: StoryTimelineFeature) -> Optional[str]:
    """None if this row can be narrated, else the reason it can't."""
    if not row.coherent:
        return "not a coherent timeline"
    if not row.context or not row.beats:
        return "no written context/beats yet"
    return None


async def set_status(row_id: int, state: str, message: str = "") -> None:
    """Record the run's state ("running" | "done" | "failed") for the admin
    page. Best-effort: Redis being down must never fail a narration run."""
    payload = json.dumps({"state": state, "message": message, "at": datetime.now(timezone.utc).isoformat()})
    ttl = RUNNING_TTL_SECONDS if state == "running" else STATUS_TTL_SECONDS
    try:
        await get_redis_client().setex(_status_key(row_id), ttl, payload)
    except Exception as e:  # noqa: BLE001
        logger.warning("could not record narration status for row %s: %s", row_id, e)


async def get_statuses(row_ids: Iterable[int]) -> dict[int, dict]:
    """{row_id: {"state", "message", "at"}} for rows with a recorded run.
    Best-effort — returns {} if Redis is unavailable."""
    ids = list(row_ids)
    if not ids:
        return {}
    try:
        values = await get_redis_client().mget([_status_key(i) for i in ids])
    except Exception as e:  # noqa: BLE001
        logger.warning("could not read narration statuses: %s", e)
        return {}
    statuses: dict[int, dict] = {}
    for row_id, value in zip(ids, values):
        if value:
            try:
                statuses[row_id] = json.loads(value)
            except ValueError:
                continue
    return statuses


async def in_progress(row_id: int) -> bool:
    """Whether a run for this row currently holds its lease — the source of
    truth for "already running", shared by every worker and server."""
    try:
        async with AsyncSessionLocal() as s:
            row = await s.execute(
                text("SELECT 1 FROM job_lease WHERE job_name = :job AND expires_at > now()"),
                {"job": lease_name(row_id)},
            )
            return row.first() is not None
    except Exception as e:  # noqa: BLE001
        logger.warning("could not check narration lease for row %s: %s", row_id, e)
        return False


async def _run(row_id: int) -> NarrationOutcome:
    # Read what the audio will be built from, then let go of the session: the
    # calls below take minutes and must not hold a connection (the pool is tiny).
    async with AsyncSessionLocal() as s:
        row = await s.get(StoryTimelineFeature, row_id)
        if row is None:
            return NarrationOutcome(False, "no such timeline")
        reason = can_narrate(row)
        if reason:
            return NarrationOutcome(False, reason)
        anchor_cluster_id = row.anchor_cluster_id
        context = row.context
        beats = copy.deepcopy(row.beats)

    try:
        spoken_script = await call_claude_spoken_script_only(context, beats)
    except TimelineNarrativeError as e:
        return NarrationOutcome(False, f"Claude could not write the spoken script: {str(e)[:300]}")

    audio = await generate_audio(anchor_cluster_id, spoken_script)
    if audio is None:
        return NarrationOutcome(
            False, "Sarvam/upload failed (see the server log); the previous audio, if any, is unchanged"
        )

    async with AsyncSessionLocal() as s:
        row = await s.get(StoryTimelineFeature, row_id)
        if row is None:
            return NarrationOutcome(False, "the timeline was removed while narrating")
        if row.context != context or row.beats != beats:
            return NarrationOutcome(False, "the timeline was regenerated while narrating; run it again")
        row.spoken_script = spoken_script
        row.audio_url = audio["audio_url"]
        row.audio_duration_seconds = audio["audio_duration_seconds"]
        row.audio_beat_offsets = audio["audio_beat_offsets"]
        row.spoken_script_hash = audio["spoken_script_hash"]
        row.audio_generated_at = datetime.now(timezone.utc)
        await s.commit()

    minutes, seconds = divmod(audio["audio_duration_seconds"], 60)
    return NarrationOutcome(True, f"narration saved ({minutes}:{seconds:02d})")


async def narrate_row(row_id: int) -> NarrationOutcome:
    """Claude script + Sarvam audio + database write for one timeline row.
    Never raises; the outcome is returned and recorded for the admin page."""
    if not is_configured():
        outcome = NarrationOutcome(False, "narration is not configured on this server (SARVAM_API_KEY / Supabase)")
        await set_status(row_id, "failed", outcome.message)
        return outcome

    async with job_lease(lease_name(row_id), ttl_seconds=LEASE_TTL_SECONDS) as held:
        if not held:
            # Another run owns this row; leave its status alone.
            return NarrationOutcome(False, "already being narrated")
        await set_status(row_id, "running")
        try:
            outcome = await _run(row_id)
        except Exception as e:  # noqa: BLE001 - a background task has no caller to raise to
            logger.exception("narration of row %s crashed", row_id)
            outcome = NarrationOutcome(False, f"unexpected error: {type(e).__name__}: {str(e)[:200]}")
        await set_status(row_id, "done" if outcome.ok else "failed", outcome.message)
        logger.info("narration of row %s: %s (%s)", row_id, "ok" if outcome.ok else "failed", outcome.message)
        return outcome
