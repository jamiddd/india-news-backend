"""Admin for Explainers: pose a question (ad hoc, from the trending
suggestions strip, or starting from a top story), attach real story clusters
as sources, generate an answer with Claude (+ optional Sarvam narration),
review/edit, and publish.

JSON API for the admin SPA's Explainers pages (app/static/admin/sections/
explainers.js). Uses the shared app.admin_session cookie like every other
section; reads need require_admin, writes require_admin_write (CSRF header).

Explainers answer from this outlet's own reporting, never the model's general
knowledge: generation is refused until at least one real StoryCluster is
attached (the source picker below), and the service builds Claude's prompt
from exactly those clusters. Generation and audio regeneration run as
background tasks guarded by the service's job lease; the SPA polls
GET /{id}/status while one is running.
"""
from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_session import require_admin, require_admin_write
from app.database import get_db
from app.models import Explainer, StoryCluster
from app.services import explainer as explainer_service
from app.services import explainer_sourcing
from app.services.cluster_search import search_clusters
from app.services.daily_brief_select import CORE_CATEGORIES, FALLBACK_CATEGORIES
from app.services.timeline_audio import is_configured as audio_is_configured
from app.services.trending import get_trending_terms

router = APIRouter(prefix="/admin/api/explainers", dependencies=[Depends(require_admin)])

CATEGORIES = CORE_CATEGORIES + FALLBACK_CATEGORIES
DEPTHS = [("quick", "Quick (~2m)"), ("standard", "Standard (~5m)"), ("deep", "Deep dive (~9m)")]
VOICES = [("shubh", "Shubh (male)"), ("simran", "Simran (female)")]
STATUSES = ("draft", "generating", "ready_for_review", "published", "archived")
STATUS_LABELS = {
    "draft": "Draft",
    "generating": "Generating",
    "ready_for_review": "Ready for review",
    "published": "Published",
    "archived": "Archived",
}

# How many matches the "from a top story" search shows at once, and the
# smaller list the review page's source picker shows.
FROM_STORY_SEARCH_LIMIT = 20
SOURCE_SEARCH_LIMIT = 8
LIST_LIMIT = 500
TRENDING_LIMIT = 5

GENERATE_CONFIRM = (
    "This calls Claude (roughly Rs 7) and takes a couple of minutes. Add narration and it also calls "
    "Sarvam (roughly Rs 3 more)."
)
REGENERATE_ALL_CONFIRM = "This discards the current draft and calls Claude again (roughly Rs 7)."
REGENERATE_AUDIO_CONFIRM = (
    "This re-voices the current answer (roughly Rs 3, Claude + Sarvam) without touching the written "
    "content, and replaces the existing narration for everyone who has already listened to or cached it."
)

Category = Literal[tuple(CATEGORIES)]  # type: ignore[valid-type]
Depth = Literal["quick", "standard", "deep"]
Voice = Literal["shubh", "simran"]


async def pending_count(db: AsyncSession) -> int:
    """Sidebar badge: explainers generated and waiting for a human."""
    return int((await db.execute(
        select(func.count()).select_from(Explainer).where(Explainer.status == "ready_for_review"))).scalar() or 0)


def _iso(value) -> Optional[str]:
    return value.isoformat() if value else None


def _summary(row: Explainer) -> dict:
    return {
        "id": row.id,
        "question": row.question,
        "category": row.category,
        "depth": row.depth,
        "status": row.status,
        "statusLabel": STATUS_LABELS.get(row.status, row.status),
        "sourceCount": len(row.source_cluster_ids or []),
        "hasAudio": bool(row.audio_url),
        "heroImageUrl": row.hero_image_url,
        "error": row.error,
        "createdAt": _iso(row.created_at),
        "updatedAt": _iso(row.updated_at),
        "publishedAt": _iso(row.published_at),
    }


def _cluster(c: StoryCluster, attached: set[int] | None = None) -> dict:
    out = {
        "id": c.id,
        "headline": c.headline,
        "summary": c.summary,
        "sourceCount": c.distinct_source_count or 0,
        "url": f"/api/v1/clusters/{c.id}",
    }
    if attached is not None:
        out["attached"] = c.id in attached
    return out


def _options() -> dict:
    return {
        "categories": [{"value": c, "label": c.title()} for c in CATEGORIES],
        "depths": [{"value": v, "label": label} for v, label in DEPTHS],
        "voices": [{"value": v, "label": label} for v, label in VOICES],
    }


async def _get_row(db: AsyncSession, explainer_id: int) -> Explainer:
    row = (await db.execute(select(Explainer).where(Explainer.id == explainer_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="That explainer doesn't exist.")
    return row


async def _get_trigger(db: AsyncSession, cluster_id: int) -> StoryCluster:
    trigger = (await db.execute(select(StoryCluster).where(StoryCluster.id == cluster_id))).scalar_one_or_none()
    if trigger is None:
        raise HTTPException(status_code=404, detail="That story doesn't exist any more.")
    return trigger


async def _running(explainer_id: int) -> tuple[bool, dict]:
    status = await explainer_service.get_status(explainer_id)
    running = status.get("state") == "generating" or await explainer_service.in_progress(explainer_id)
    return running, status


# ---------- list ----------

@router.get("")
async def list_explainers(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(Explainer).order_by(desc(Explainer.updated_at)).limit(LIST_LIMIT))).scalars().all()
    counts = dict((await db.execute(
        select(Explainer.status, func.count()).group_by(Explainer.status))).all())
    terms = await get_trending_terms(TRENDING_LIMIT)
    return {
        "counts": {s: counts.get(s, 0) for s in STATUSES},
        "items": [_summary(r) for r in rows],
        "suggestions": [{"term": term, "count": count} for term, count in terms],
    }


@router.get("/options")
async def options():
    return {**_options(), "audioConfigured": audio_is_configured()}


# ---------- create ----------

class CreateIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    category: Optional[Category] = None
    depth: Depth = "standard"
    admin_notes: Optional[str] = Field(default=None, alias="adminNotes", max_length=4000)

    model_config = {"populate_by_name": True}


def _clean(body: CreateIn) -> tuple[str, str, str, Optional[str]]:
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="Write the question first.")
    return question, body.category or CATEGORIES[0], body.depth, (body.admin_notes or "").strip() or None


@router.post("", dependencies=[Depends(require_admin_write)])
async def create(body: CreateIn, db: AsyncSession = Depends(get_db)):
    question, category, depth, admin_notes = _clean(body)
    row = Explainer(question=question, category=category, depth=depth, admin_notes=admin_notes)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {"ok": True, "id": row.id}


@router.get("/from-story")
async def from_story_search(q: str = "", limit: int = FROM_STORY_SEARCH_LIMIT,
                            explainer_id: Optional[int] = None, db: AsyncSession = Depends(get_db)):
    """Story search shared by the "from a top story" page and the review
    page's source picker (which passes explainer_id so already-attached
    stories are flagged)."""
    q = q.strip()
    if not q:
        return {"q": "", "items": []}
    limit = max(1, min(limit, FROM_STORY_SEARCH_LIMIT))
    attached = None
    if explainer_id is not None:
        row = await _get_row(db, explainer_id)
        attached = set(row.source_cluster_ids or [])
    clusters = await search_clusters(db, q, limit=limit)
    return {"q": q, "items": [_cluster(c, attached) for c in clusters]}


@router.get("/from-story/{cluster_id}")
async def from_story_detail(cluster_id: int, db: AsyncSession = Depends(get_db)):
    trigger = await _get_trigger(db, cluster_id)
    return {
        "story": _cluster(trigger),
        "suggestedCategory": await explainer_sourcing.suggest_category(db, trigger),
        **_options(),
    }


@router.post("/from-story/{cluster_id}", dependencies=[Depends(require_admin_write)])
async def from_story_create(cluster_id: int, body: CreateIn, db: AsyncSession = Depends(get_db)):
    trigger = await _get_trigger(db, cluster_id)
    question, category, depth, admin_notes = _clean(body)
    background = await explainer_sourcing.suggest_background_clusters(db, trigger, question=question)
    # The trigger story itself always leads the source list, then whatever
    # background suggest_background_clusters found, oldest first.
    source_cluster_ids = [trigger.id] + [c.id for c in background]
    row = Explainer(question=question, category=category, depth=depth, admin_notes=admin_notes,
                    source_cluster_ids=source_cluster_ids)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {"ok": True, "id": row.id, "sourceCount": len(source_cluster_ids)}


# ---------- one explainer ----------

@router.get("/{explainer_id}")
async def detail(explainer_id: int, db: AsyncSession = Depends(get_db)):
    row = await _get_row(db, explainer_id)
    running, status = await _running(explainer_id)
    attached_ids = list(row.source_cluster_ids or [])
    attached = await explainer_service.fetch_source_clusters(attached_ids) if row.status in ("draft", "generating") else []
    return {
        **_summary(row),
        "adminNotes": row.admin_notes,
        "quickAnswer": row.quick_answer,
        "sections": [{"heading": s.get("heading", ""), "body": s.get("body", "")} for s in (row.sections or [])],
        "sources": [{
            "clusterId": s.get("cluster_id"),
            "title": s.get("title"),
            "outlet": s.get("outlet"),
            "url": s.get("url"),
            "sourceCount": s.get("source_count"),
        } for s in (row.sources or [])],
        "sourceClusterIds": attached_ids,
        "attachedSources": [_cluster(c) for c in attached],
        "audioUrl": row.audio_url,
        "audioDurationSeconds": row.audio_duration_seconds,
        "voice": row.voice,
        "generationCost": row.generation_cost,
        "publicUrl": f"/api/v1/explainers/{row.id}" if row.status == "published" else None,
        "running": running,
        "progress": status.get("message", "") if running else "",
        "lastRun": {"state": status.get("state"), "message": status.get("message", "")} if status else None,
        "audioConfigured": audio_is_configured(),
        "confirm": {
            "generate": GENERATE_CONFIRM,
            "regenerateAll": REGENERATE_ALL_CONFIRM,
            "regenerateAudio": REGENERATE_AUDIO_CONFIRM,
        },
        **_options(),
    }


@router.get("/{explainer_id}/status")
async def status_json(explainer_id: int):
    running, status = await _running(explainer_id)
    return {
        "explainerId": explainer_id,
        "state": "generating" if running else status.get("state", "idle"),
        "message": status.get("message", ""),
    }


class SourceIn(BaseModel):
    cluster_id: int = Field(alias="clusterId")

    model_config = {"populate_by_name": True}


@router.post("/{explainer_id}/sources/add", dependencies=[Depends(require_admin_write)])
async def add_source(explainer_id: int, body: SourceIn, db: AsyncSession = Depends(get_db)):
    await _get_row(db, explainer_id)
    await explainer_service.add_source(explainer_id, body.cluster_id)
    return {"ok": True}


@router.post("/{explainer_id}/sources/remove", dependencies=[Depends(require_admin_write)])
async def remove_source(explainer_id: int, body: SourceIn, db: AsyncSession = Depends(get_db)):
    await _get_row(db, explainer_id)
    await explainer_service.remove_source(explainer_id, body.cluster_id)
    return {"ok": True}


class GenerateIn(BaseModel):
    narrate: bool = False
    voice: Voice = "shubh"


@router.post("/{explainer_id}/generate", dependencies=[Depends(require_admin_write)])
async def generate(explainer_id: int, body: GenerateIn, background_tasks: BackgroundTasks,
                   db: AsyncSession = Depends(get_db)):
    row = await _get_row(db, explainer_id)
    if await explainer_service.in_progress(explainer_id):
        raise HTTPException(status_code=409, detail="This explainer is already generating.")
    if not row.source_cluster_ids:
        raise HTTPException(status_code=422, detail="Attach at least one source before generating.")
    await explainer_service.set_status(explainer_id, "generating", "starting")
    background_tasks.add_task(explainer_service.run_build_task, explainer_id, narrate=body.narrate, voice=body.voice)
    return {"ok": True, "state": "generating"}


class AudioIn(BaseModel):
    voice: Voice = "shubh"


@router.post("/{explainer_id}/regenerate-audio", dependencies=[Depends(require_admin_write)])
async def regenerate_audio(explainer_id: int, body: AudioIn, background_tasks: BackgroundTasks,
                           db: AsyncSession = Depends(get_db)):
    await _get_row(db, explainer_id)
    if not audio_is_configured():
        raise HTTPException(status_code=422, detail="Audio isn't configured on this server.")
    if await explainer_service.in_progress(explainer_id):
        raise HTTPException(status_code=409, detail="This explainer is already generating.")
    await explainer_service.set_status(explainer_id, "generating", "starting")
    background_tasks.add_task(explainer_service.run_regenerate_audio_task, explainer_id, voice=body.voice)
    return {"ok": True, "state": "generating"}


class SectionIn(BaseModel):
    section_index: int = Field(alias="sectionIndex", ge=0)

    model_config = {"populate_by_name": True}


@router.post("/{explainer_id}/regenerate-section", dependencies=[Depends(require_admin_write)])
async def regenerate_section(explainer_id: int, body: SectionIn, db: AsyncSession = Depends(get_db)):
    row = await _get_row(db, explainer_id)
    if body.section_index >= len(row.sections or []):
        raise HTTPException(status_code=422, detail="That section doesn't exist.")
    if not await explainer_service.regenerate_section(explainer_id, body.section_index):
        raise HTTPException(status_code=502, detail="Claude couldn't rewrite that section. Try again.")
    return {"ok": True}


@router.post("/{explainer_id}/publish", dependencies=[Depends(require_admin_write)])
async def publish(explainer_id: int, db: AsyncSession = Depends(get_db)):
    row = await _get_row(db, explainer_id)
    if row.status != "ready_for_review":
        raise HTTPException(status_code=409, detail="Only an explainer that is ready for review can be published.")
    await explainer_service.publish(explainer_id)
    return {"ok": True, "status": "published"}


@router.post("/{explainer_id}/archive", dependencies=[Depends(require_admin_write)])
async def archive(explainer_id: int, db: AsyncSession = Depends(get_db)):
    await _get_row(db, explainer_id)
    await explainer_service.archive(explainer_id)
    return {"ok": True, "status": "archived"}


@router.post("/{explainer_id}/restore", dependencies=[Depends(require_admin_write)])
async def restore(explainer_id: int, db: AsyncSession = Depends(get_db)):
    row = await _get_row(db, explainer_id)
    if row.status != "archived":
        raise HTTPException(status_code=409, detail="Only an archived explainer can be restored.")
    await explainer_service.restore(explainer_id)
    return {"ok": True, "status": "published"}
