"""Timeline/Context tab editorial picks: JSON API for the admin SPA's
Timelines page (app/static/admin/sections/timelines.js).

This is the human-intervention step (see StoryTimelineFeature's docstring):
make or remove editorial picks (with a headline search to find a cluster_id),
start narration for a timeline, and override its lead image. Uses the shared
app.admin_session cookie like every other section; reads need require_admin,
writes require_admin_write (CSRF header). (It once wrapped bare JSON endpoints
in main.py — /admin/timelines/picks, /pick, /unpick — which have since been
removed; see the note near main.py's timeline admin section.)

There is no sidebar badge (no `pending_count`): picks are optional and the
generation script fills empty slots on its own, so nothing here ever waits on
a human.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.admin_session import require_admin, require_admin_write
from app.database import get_db
from app.models import Article, StoryCluster, StoryTimelineFeature, utc_now
from app.redis_client import get_redis_client
from app.services import timeline_narration as narration
from app.services.image_extractor import is_hd_image
from app.services.timeline_audio import SCRIPT_VERSION, is_configured

router = APIRouter(prefix="/admin/api/timelines", dependencies=[Depends(require_admin)])

SEARCH_LIMIT = 15
SLOTS = 5

# Shown by the SPA in its confirm dialog, kept here so the cost estimate
# lives next to the endpoint that spends it.
NARRATE_CONFIRM = (
    "This calls Claude and Sarvam (roughly Rs 15-20 for a typical story), takes a few minutes, "
    "and replaces its current audio only if it succeeds."
)
NOT_ELIGIBLE = "That timeline can't be narrated (it isn't a coherent timeline with written beats yet)."
NOT_CONFIGURED = "Narration isn't configured on this server (SARVAM_API_KEY / Supabase storage)."
ALREADY_RUNNING = "That timeline is already being narrated."


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _label(row: StoryTimelineFeature) -> str:
    return row.title or row.anchor_label or f"Cluster {row.anchor_cluster_id}"


def _audio_is_current(row: StoryTimelineFeature) -> bool:
    return bool(row.audio_url) and (row.spoken_script or {}).get("version") == SCRIPT_VERSION


def _row_json(row: StoryTimelineFeature, status: dict | None) -> dict:
    status = status or {}
    return {
        "id": row.id,
        "label": _label(row),
        "title": row.title,
        "anchorLabel": row.anchor_label,
        "anchorClusterId": row.anchor_cluster_id,
        "clusterUrl": f"/api/v1/clusters/{row.anchor_cluster_id}",
        "timelineUrl": f"/api/v1/timelines/{row.id}",
        "isEditorialPick": bool(row.is_editorial_pick),
        "lastSeenInTop": bool(row.last_seen_in_top),
        "droppedFromTopAt": _iso(row.dropped_from_top_at),
        # None = never judged yet; False = the chain stopped cohering.
        "coherent": row.coherent,
        "context": row.context,
        "beatCount": len(row.beats or []),
        "chainLength": len(row.cluster_ids or []),
        "narrativeGeneratedAt": _iso(row.narrative_generated_at),
        "pickedAt": _iso(row.picked_at),
        "updatedAt": _iso(row.updated_at),
        "viewCount": row.view_count or 0,
        "manualImageUrl": row.manual_image_url,
        "audio": {
            "url": row.audio_url,
            "durationSeconds": row.audio_duration_seconds or 0,
            "generatedAt": _iso(row.audio_generated_at),
            "isCurrent": _audio_is_current(row),
        } if row.audio_url else None,
        "narration": {
            "state": status.get("state", "idle"),
            "message": status.get("message", ""),
            "at": status.get("at"),
        },
        # Why the narrate button is disabled, or None if it can run.
        "narrateBlocked": narration.can_narrate(row),
    }


@router.get("")
async def list_timelines(q: str = "", db: AsyncSession = Depends(get_db)):
    q = q.strip()
    picks = (await db.execute(
        select(StoryTimelineFeature).order_by(
            desc(StoryTimelineFeature.is_editorial_pick), desc(StoryTimelineFeature.picked_at))
    )).scalars().all()
    picked_ids = {row.anchor_cluster_id for row in picks}

    search = None
    if q:
        clusters = (await db.execute(
            select(StoryCluster).where(StoryCluster.headline.ilike(f"%{q}%"))
            .order_by(desc(StoryCluster.last_updated_at)).limit(SEARCH_LIMIT)
        )).scalars().all()
        search = {"q": q, "results": [{
            "id": c.id,
            "headline": c.headline,
            "sourceCount": c.distinct_source_count or 0,
            "url": f"/api/v1/clusters/{c.id}",
        } for c in clusters if c.id not in picked_ids]}

    statuses = await narration.get_statuses(row.id for row in picks)
    rows = [_row_json(row, statuses.get(row.id)) for row in picks]
    return {
        "slots": SLOTS,
        "configured": is_configured(),
        "narrateConfirm": NARRATE_CONFIRM,
        # A run is started on one worker/server and finishes minutes later,
        # so while any is in flight the SPA polls (statuses are shared).
        "anyRunning": any(r["narration"]["state"] == "running" for r in rows),
        "rows": rows,
        "search": search,
    }


class UpdateIn(BaseModel):
    clusterId: int
    action: Literal["pick", "unpick"]


@router.post("/update", dependencies=[Depends(require_admin_write)])
async def update(body: UpdateIn, db: AsyncSession = Depends(get_db)):
    cluster_id = body.clusterId
    if body.action == "pick":
        cluster = await db.get(StoryCluster, cluster_id)
        if cluster is None:
            raise HTTPException(status_code=404, detail="That cluster doesn't exist.")
        statement = pg_insert(StoryTimelineFeature).values(
            anchor_cluster_id=cluster_id, is_editorial_pick=True,
        ).on_conflict_do_update(
            index_elements=["anchor_cluster_id"],
            set_={"is_editorial_pick": True, "updated_at": utc_now()},
        )
        await db.execute(statement)
    else:
        row = await db.scalar(
            select(StoryTimelineFeature).where(StoryTimelineFeature.anchor_cluster_id == cluster_id))
        if row is None:
            raise HTTPException(status_code=404, detail="No pick found for that cluster.")
        row.is_editorial_pick = False
        row.updated_at = utc_now()

    await db.commit()
    return {"ok": True, "clusterId": cluster_id, "action": body.action}


class NarrateIn(BaseModel):
    rowId: int


@router.post("/narrate", dependencies=[Depends(require_admin_write)])
async def narrate(body: NarrateIn, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    """Start narrating one timeline (Claude writes the spoken script, Sarvam
    voices it, the row is updated). Returns at once — the work takes minutes,
    so it runs as a background task and the SPA polls its status."""
    row_id = body.rowId
    row = await db.get(StoryTimelineFeature, row_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Timeline not found.")

    if narration.can_narrate(row):
        raise HTTPException(status_code=409, detail=NOT_ELIGIBLE)
    if not is_configured():
        raise HTTPException(status_code=503, detail=NOT_CONFIGURED)
    if await narration.in_progress(row_id):
        raise HTTPException(status_code=409, detail=ALREADY_RUNNING)

    # Marked running before the response so the reloaded page already shows
    # it; the task itself takes the per-row lease that makes a second click a no-op.
    await narration.set_status(row_id, "running")
    background_tasks.add_task(narration.narrate_row, row_id)
    return {"ok": True, "rowId": row_id, "state": "running"}


@router.get("/narrate/{row_id}")
async def narration_status(row_id: int, db: AsyncSession = Depends(get_db)):
    """Status of a narration run, for polling."""
    row = await db.get(StoryTimelineFeature, row_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Timeline not found.")
    status = (await narration.get_statuses([row_id])).get(row_id) or {}
    running = await narration.in_progress(row_id) or status.get("state") == "running"
    return {
        "rowId": row_id,
        "state": "running" if running else status.get("state", "idle"),
        "message": status.get("message", ""),
        "at": status.get("at"),
        "hasAudio": bool(row.audio_url),
        "audioIsCurrent": _audio_is_current(row),
        "audioGeneratedAt": _iso(row.audio_generated_at),
    }


async def _invalidate_timeline_caches(row_id: int) -> None:
    # GET /timelines, /timelines/archived, and /timelines/{id} each cache
    # under these keys (see app/main.py) — without this, a manual image
    # pick/clear wouldn't be visible in the app until CACHE_TTL_SECONDS
    # expires. Same fail-open convention as _cache_get/_cache_set: caching
    # is a perf optimization, never a correctness dependency. Keys must be
    # kept in sync with main.py's — see that module's comments for the
    # version history.
    try:
        client = get_redis_client()
        await client.delete("timelines:list:v6")
        await client.delete("timelines:archived:v6")
        await client.delete(f"timelines:{row_id}:v5")
    except Exception:
        pass


async def _gather_image_candidates(db: AsyncSession, row: StoryTimelineFeature) -> list[Article]:
    """Every distinct-by-image_url article across this timeline's whole
    chain (not just the current hero cluster — the admin should be able to
    pick any photo any member outlet ran), sorted the same way the
    auto-selector ranks them (HD first, then most recent) so the best
    candidates surface first."""
    cluster_ids = row.cluster_ids or []
    if not cluster_ids:
        return []
    result = await db.execute(
        select(StoryCluster)
        .where(StoryCluster.id.in_(cluster_ids))
        .options(selectinload(StoryCluster.articles).selectinload(Article.source))
    )
    seen_urls: set[str] = set()
    candidates: list[Article] = []
    for cluster in result.scalars().all():
        for article in cluster.articles:
            if not article.image_url or article.image_url in seen_urls:
                continue
            seen_urls.add(article.image_url)
            candidates.append(article)
    candidates.sort(
        key=lambda a: (is_hd_image(a.image_width, a.image_height), a.published_at),
        reverse=True,
    )
    return candidates


async def _get_row(db: AsyncSession, row_id: int) -> StoryTimelineFeature:
    row = await db.get(StoryTimelineFeature, row_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Timeline not found.")
    return row


@router.get("/image/{row_id}")
async def image_candidates(row_id: int, db: AsyncSession = Depends(get_db)):
    """Lets an admin override the auto-selected lead image (list feed hero
    + detail cover art — see StoryTimelineFeature.manual_image_url) with
    any photo actually carried by an article somewhere in this timeline's
    chain, rather than trusting the HD-then-recency auto-pick every time."""
    row = await _get_row(db, row_id)
    candidates = await _gather_image_candidates(db, row)
    return {
        "id": row.id,
        "label": _label(row),
        "anchorClusterId": row.anchor_cluster_id,
        "manualImageUrl": row.manual_image_url,
        "candidates": [{
            "imageUrl": a.image_url,
            "source": a.source.name if a.source else "Unknown",
            "publishedAt": _iso(a.published_at),
            "hd": bool(is_hd_image(a.image_width, a.image_height)),
            "current": a.image_url == row.manual_image_url,
        } for a in candidates],
    }


class ImageIn(BaseModel):
    imageUrl: str


@router.post("/image/{row_id}", dependencies=[Depends(require_admin_write)])
async def set_image(row_id: int, body: ImageIn, db: AsyncSession = Depends(get_db)):
    row = await _get_row(db, row_id)
    # Re-derive the candidate set server-side rather than trusting the
    # posted URL outright — it must be a real image already carried by some
    # article in this chain, not an arbitrary string a client could be made
    # to submit.
    candidates = await _gather_image_candidates(db, row)
    if body.imageUrl not in {a.image_url for a in candidates}:
        raise HTTPException(status_code=400, detail="That image isn't carried by any article in this timeline.")

    row.manual_image_url = body.imageUrl
    row.updated_at = utc_now()
    await db.commit()
    await _invalidate_timeline_caches(row_id)
    return {"ok": True, "id": row_id, "manualImageUrl": body.imageUrl}


@router.post("/image/{row_id}/clear", dependencies=[Depends(require_admin_write)])
async def clear_image(row_id: int, db: AsyncSession = Depends(get_db)):
    row = await _get_row(db, row_id)
    row.manual_image_url = None
    row.updated_at = utc_now()
    await db.commit()
    await _invalidate_timeline_caches(row_id)
    return {"ok": True, "id": row_id, "manualImageUrl": None}
