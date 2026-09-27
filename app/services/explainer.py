"""Builds one Explainer: have Claude write the answer, optionally voice it
with Sarvam, and store the result on the Explainer row.

Run by the admin "Generate"/"Regenerate" buttons in admin_explainers.py, via
BackgroundTasks — there is no scheduled cycle (unlike Daily Brief/Timelines),
since every explainer starts from an admin-posed question rather than a fixed
daily selection.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import Explainer
from app.redis_client import get_redis_client
from app.services import explainer_script, timeline_audio
from app.services.job_lease import job_lease

logger = logging.getLogger(__name__)

CACHE_KEY_LIST = "explainers:list:v1"
LEASE_JOB_PREFIX = "explainer"
LEASE_TTL_SECONDS = 10 * 60
STATUS_TTL_SECONDS = 24 * 3600
RUNNING_TTL_SECONDS = 10 * 60

# Rough, told-to-the-admin cost estimate (Claude Sonnet call plus, if narrated,
# a Sarvam TTS call) — not billed/metered precisely, matching the Daily
# Brief admin page's "roughly Rs 8" framing rather than an exact ledger.
BASE_GENERATION_COST = 7.0
NARRATION_COST = 3.0


def _lease_job(explainer_id: int) -> str:
    return f"{LEASE_JOB_PREFIX}:{explainer_id}"


def _status_key(explainer_id: int) -> str:
    return f"explainer_status:{explainer_id}"


async def set_status(explainer_id: int, state: str, message: str = "") -> None:
    payload = json.dumps({"state": state, "message": message, "at": datetime.now(timezone.utc).isoformat()})
    ttl = RUNNING_TTL_SECONDS if state == "generating" else STATUS_TTL_SECONDS
    try:
        await get_redis_client().setex(_status_key(explainer_id), ttl, payload)
    except Exception as e:  # noqa: BLE001
        logger.warning("could not record explainer status for %s: %s", explainer_id, e)


async def get_status(explainer_id: int) -> dict:
    try:
        value = await get_redis_client().get(_status_key(explainer_id))
    except Exception as e:  # noqa: BLE001
        logger.warning("could not read explainer status: %s", e)
        return {}
    return json.loads(value) if value else {}


async def in_progress(explainer_id: int) -> bool:
    from sqlalchemy import text
    try:
        async with AsyncSessionLocal() as s:
            row = await s.execute(
                text("SELECT 1 FROM job_lease WHERE job_name = :job AND expires_at > now()"),
                {"job": _lease_job(explainer_id)},
            )
            return row.first() is not None
    except Exception as e:  # noqa: BLE001
        logger.warning("could not check explainer lease for %s: %s", explainer_id, e)
        return False


async def invalidate_list_cache() -> None:
    try:
        await get_redis_client().delete(CACHE_KEY_LIST)
    except Exception:  # noqa: BLE001
        pass


async def _update(explainer_id: int, **fields) -> None:
    async with AsyncSessionLocal() as session:
        row = (await session.execute(select(Explainer).where(Explainer.id == explainer_id))).scalar_one_or_none()
        if row is None:
            return
        for key, value in fields.items():
            setattr(row, key, value)
        await session.commit()


async def run_build_task(explainer_id: int, *, narrate: bool = False, voice: Optional[str] = None) -> None:
    """build_explainer as a fire-and-forget task (the admin's Generate
    button): whatever happens, status ends as ready_for_review/error, never
    stuck on "generating"."""
    try:
        await build_explainer(explainer_id, narrate=narrate, voice=voice)
    except Exception as exc:  # noqa: BLE001
        logger.exception("explainer %s crashed", explainer_id)
        await _update(explainer_id, status="draft", error=f"{type(exc).__name__}: {exc}")
        await set_status(explainer_id, "error", str(exc))


async def build_explainer(explainer_id: int, *, narrate: bool = False, voice: Optional[str] = None) -> bool:
    """Generate (or regenerate) the full explainer. Returns True on success.
    Never raises for an expected failure — records status/error and returns
    False; unexpected exceptions propagate to run_build_task's handler."""
    async with job_lease(_lease_job(explainer_id), LEASE_TTL_SECONDS) as held:
        if not held:
            await set_status(explainer_id, "error", "another generation is already running")
            return False

        async with AsyncSessionLocal() as session:
            row = (await session.execute(select(Explainer).where(Explainer.id == explainer_id))).scalar_one_or_none()
        if row is None:
            await set_status(explainer_id, "error", "explainer not found")
            return False

        await set_status(explainer_id, "generating", "writing answer")
        await _update(explainer_id, status="generating", error=None)

        answer = await explainer_script.write_explainer(row.question, row.category, row.depth, row.admin_notes)
        if answer is None:
            await _update(explainer_id, status="draft", error="Claude did not return a valid explainer")
            await set_status(explainer_id, "error", "Claude did not return a valid explainer")
            return False

        audio_fields: dict = {"audio_url": None, "audio_duration_seconds": None, "voice": None}
        cost = BASE_GENERATION_COST
        if narrate and timeline_audio.is_configured():
            await set_status(explainer_id, "generating", "voicing")
            speaker = voice or "shubh"
            chunks = [answer["quick_answer"]] + [s["body"] for s in answer["sections"]]
            rendered = await timeline_audio.render_and_upload(
                f"explainer {explainer_id}", chunks, 0,
                f"explainers/{explainer_id}.{timeline_audio.AUDIO_FILE_EXTENSION}",
                speaker=speaker,
            )
            if rendered is not None:
                url, duration_seconds, _offsets = rendered
                audio_fields = {"audio_url": url, "audio_duration_seconds": duration_seconds, "voice": speaker}
                cost += NARRATION_COST
            else:
                logger.warning("explainer %s: narration requested but voicing failed; publishing without audio", explainer_id)
        elif narrate:
            logger.warning("explainer %s: narration requested but audio is not configured", explainer_id)

        await _update(
            explainer_id,
            status="ready_for_review",
            quick_answer=answer["quick_answer"],
            sections=answer["sections"],
            sources=answer["sources"],
            generation_cost=cost,
            error=None,
            **audio_fields,
        )
        await set_status(explainer_id, "done", "ready for review")
        return True


async def regenerate_section(explainer_id: int, section_index: int) -> bool:
    """Regenerate one section's body in place, keeping its heading and
    leaving quick_answer/sources/audio untouched."""
    async with AsyncSessionLocal() as session:
        row = (await session.execute(select(Explainer).where(Explainer.id == explainer_id))).scalar_one_or_none()
    if row is None or not row.sections or not (0 <= section_index < len(row.sections)):
        return False

    heading = row.sections[section_index]["heading"]
    body = await explainer_script.write_section(row.question, row.category, heading, row.admin_notes)
    if body is None:
        return False

    sections = list(row.sections)
    sections[section_index] = {"heading": heading, "body": body}
    await _update(explainer_id, sections=sections)
    return True


async def publish(explainer_id: int) -> None:
    await _update(explainer_id, status="published", published_at=datetime.now(timezone.utc))
    await invalidate_list_cache()


async def archive(explainer_id: int) -> None:
    await _update(explainer_id, status="archived")
    await invalidate_list_cache()


async def restore(explainer_id: int) -> None:
    """Back to published — restoring an archived explainer republishes it
    rather than returning it to draft, matching what "Restore" implies on
    the dashboard (see the approved Explainers design mockup)."""
    await _update(explainer_id, status="published")
    await invalidate_list_cache()
