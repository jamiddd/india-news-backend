"""Daily Brief and Late-Night Wrap-up: JSON API for the admin SPA's Daily
Brief page (app/static/admin/sections/daily-brief.js). See the latest of each
kind (stories, on-screen and spoken summaries, audio) and rebuild either.

Uses the shared app.admin_session cookie like every other section; reads need
require_admin, writes require_admin_write (CSRF header). There is no sidebar
badge: both kinds are built on a schedule (05:00 and 19:30 IST) and a rebuild
is only ever an editor's choice.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_session import require_admin, require_admin_write
from app.database import get_db
from app.models import DailyBrief
from app.services import daily_brief
from app.services.timeline_audio import is_configured

router = APIRouter(prefix="/admin/api/daily-brief", dependencies=[Depends(require_admin)])
IST = ZoneInfo("Asia/Kolkata")

KINDS = {"brief": "Daily Brief", "wrapup": "Late-Night Wrap-up"}

# Shown verbatim by the SPA's confirm dialog. (The old HTML page once put
# these in a single-quoted JS string, where the apostrophes in "tonight's"
# ended the string early and a ~Rs 8 rebuild went out on a single click; JSON
# transport makes that class of bug impossible.)
REBUILD_CONFIRM = {
    "brief": "This calls Claude and Sarvam (roughly Rs 8) and takes a few minutes. When it finishes it "
             "replaces the live brief; if voicing fails it publishes text-only and the live brief loses "
             "its audio.",
    "wrapup": "This calls Claude and Sarvam (roughly Rs 8) and takes a few minutes. When it finishes it "
              "replaces the live wrap-up; if voicing fails it publishes text-only and the live wrap-up "
              "loses its audio.",
}
ALREADY_RUNNING = "A build of this kind is already running."
NOT_CONFIGURED = ("Audio isn't configured on this server (SARVAM_API_KEY / Supabase storage); "
                  "rebuilding is disabled until it is configured.")
SCHEDULE = ("Daily Brief built automatically at 05:00 IST from yesterday's stories; "
            "Late-Night Wrap-up at 19:30 IST from today's stories up to 7 PM.")


def _kind(kind: str) -> str:
    return kind if kind in KINDS else "brief"


def _brief_json(row: DailyBrief) -> dict:
    script = row.script or {}
    spoken = {i.get("cluster_id"): i.get("spoken", "") for i in script.get("items", [])}
    return {
        "id": row.id,
        "kind": row.kind,
        "briefDate": row.brief_date.isoformat(),
        "status": row.status,
        "error": row.error,
        "generatedAt": row.generated_at.isoformat() if row.generated_at else None,
        "audio": {
            "url": row.audio_url,
            "durationSeconds": row.audio_duration_seconds or 0,
        } if row.audio_url else None,
        "intro": script.get("intro", ""),
        "closing": script.get("closing", ""),
        "items": [{
            "clusterId": item["cluster_id"],
            "clusterUrl": f"/api/v1/clusters/{item['cluster_id']}",
            "headline": item.get("headline", ""),
            "category": item.get("category", ""),
            "sourceCount": item.get("source_count", 0),
            "slotKind": item.get("slot_kind"),
            "audioOffset": item.get("audio_offset"),
            "imageUrl": item.get("image_url"),
            "summary": item.get("summary", ""),
            "spoken": spoken.get(item["cluster_id"], ""),
        } for item in (row.items or [])],
    }


async def _status(kind: str) -> dict:
    today = datetime.now(IST).date()
    status = await daily_brief.get_status(today, kind)
    running = await daily_brief.in_progress(kind) or status.get("state") == "running"
    return {
        "briefDate": today.isoformat(),
        "kind": kind,
        "state": "running" if running else status.get("state", "idle"),
        "message": status.get("message", ""),
        "at": status.get("at"),
    }


@router.get("")
async def overview(kind: str = "brief", db: AsyncSession = Depends(get_db)):
    kind = _kind(kind)
    today = datetime.now(IST).date()
    row = (
        await db.execute(
            select(DailyBrief).where(DailyBrief.kind == kind).order_by(desc(DailyBrief.brief_date)).limit(1)
        )
    ).scalar_one_or_none()
    return {
        "kind": kind,
        "kinds": KINDS,
        "today": today.isoformat(),
        "schedule": SCHEDULE,
        "configured": is_configured(),
        "notConfiguredText": NOT_CONFIGURED,
        "confirm": REBUILD_CONFIRM[kind],
        # "Rebuild" once today's row of this kind exists, else "Build".
        "builtToday": bool(row and row.brief_date == today),
        "status": await _status(kind),
        "brief": _brief_json(row) if row else None,
    }


@router.get("/status")
async def status_json(kind: str = "brief"):
    """Status of today's build, for polling."""
    return await _status(_kind(kind))


class RebuildIn(BaseModel):
    kind: Literal["brief", "wrapup"] = "brief"


@router.post("/rebuild", dependencies=[Depends(require_admin_write)])
async def rebuild(body: RebuildIn, background_tasks: BackgroundTasks):
    kind = body.kind
    if await daily_brief.in_progress(kind):
        raise HTTPException(status_code=409, detail=ALREADY_RUNNING)
    if not is_configured():
        raise HTTPException(status_code=503, detail=NOT_CONFIGURED)
    today = datetime.now(IST).date()
    # Marked running before the response so the reloaded page already shows
    # it; the task itself takes the lease that makes a second click a no-op.
    await daily_brief.set_status(today, kind, "running", "starting")
    background_tasks.add_task(daily_brief.run_build_task, today, kind, force=True)
    return {"ok": True, "kind": kind, "briefDate": today.isoformat(), "state": "running"}
