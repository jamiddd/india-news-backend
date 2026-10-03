"""Human-review queue for the "Breaking" slot — see
backend/docs/breaking-human-review-plan.md and app.services.breaking's
docstring. app.services.breaking only ever writes pending_review /
pending rows (pure SQL, no LLM call); THIS module is where a human's
approve decision actually triggers the LLM narrative pass. A reject never
calls the LLM at all.

JSON API for the admin SPA's Breaking page (app/static/admin/sections/
breaking.js). Uses the shared app.admin_session cookie like every other
section; reads need require_admin, writes require_admin_write (CSRF header).

Three things live here:
  * the review queue: new candidates (approve = write the narrative, reject =
    echo) and refresh reviews for live stories (approve = append beats,
    reject = skip this batch), each with the article evidence a reviewer
    needs to make the call;
  * the list of live stories;
  * the lead-image picker, which overrides the auto-selected image with one
    actually carried by an article on the story's cluster.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_session import require_admin, require_admin_write
from app.database import get_db
from app.models import Article, BreakingRefreshReview, BreakingStory, Source, StoryCluster, utc_now
from app.redis_client import get_redis_client
from app.services.breaking import _fetch_articles_for_prompt
from app.services.image_extractor import is_hd_image

router = APIRouter(prefix="/admin/api/breaking", dependencies=[Depends(require_admin)])

# How many article rows to show per candidate/refresh — enough to sanity-
# check the judgement, not a re-read of the whole cluster. See the design
# discussion: this has to stay a fast glance.
PREVIEW_LIMIT = 25


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:  # SQLite hands back naive UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


async def pending_count(db: AsyncSession) -> int:
    """Sidebar badge: candidates plus refresh reviews waiting on a human."""
    candidates = await db.scalar(
        select(func.count()).select_from(BreakingStory).where(BreakingStory.status == "pending_review")) or 0
    refreshes = await db.scalar(
        select(func.count()).select_from(BreakingRefreshReview)
        .where(BreakingRefreshReview.status == "pending")) or 0
    return int(candidates) + int(refreshes)


def _articles_out(articles: list[dict]) -> dict:
    shown = articles[:PREVIEW_LIMIT]
    sources = sorted({a["source_name"] for a in articles if a["source_name"]})
    return {
        "articleCount": len(articles),
        "sources": sources,
        "articles": [{
            "id": a["id"],
            "publishedAt": _iso(a["published_at"]),
            "sourceName": a["source_name"],
            "title": a["title"],
        } for a in shown],
        "omitted": max(0, len(articles) - len(shown)),
    }


async def _headlines(db: AsyncSession, cluster_ids: set[int]) -> dict[int, str]:
    if not cluster_ids:
        return {}
    rows = (await db.execute(
        select(StoryCluster.id, StoryCluster.headline).where(StoryCluster.id.in_(cluster_ids)))).all()
    return {cid: headline for cid, headline in rows}


@router.get("")
async def dashboard(db: AsyncSession = Depends(get_db)):
    live = (await db.execute(
        select(BreakingStory).where(BreakingStory.status == "active")
        .order_by(desc(BreakingStory.promoted_at))
    )).scalars().all()
    candidates = (await db.execute(
        select(BreakingStory).where(BreakingStory.status == "pending_review")
        .order_by(desc(BreakingStory.promoted_at))
    )).scalars().all()
    refreshes = (await db.execute(
        select(BreakingRefreshReview).where(BreakingRefreshReview.status == "pending")
        .order_by(desc(BreakingRefreshReview.created_at))
    )).scalars().all()
    refresh_stories = {}
    if refreshes:
        rows = (await db.execute(
            select(BreakingStory).where(BreakingStory.cluster_id.in_([r.cluster_id for r in refreshes]))
        )).scalars().all()
        refresh_stories = {row.cluster_id: row for row in rows}

    headlines = await _headlines(
        db, {r.cluster_id for r in live} | {r.cluster_id for r in candidates} | {r.cluster_id for r in refreshes})

    live_out = [{
        "clusterId": row.cluster_id,
        "title": row.title or headlines[row.cluster_id],
        "headline": headlines[row.cluster_id],
        "beatCount": len(row.beats or []),
        "promotedAt": _iso(row.promoted_at),
        "lastBeatAt": _iso(row.last_beat_at),
        "manualImageUrl": row.manual_image_url,
        "url": f"/api/v1/clusters/{row.cluster_id}",
    } for row in live if row.cluster_id in headlines]

    candidates_out = []
    for row in candidates:
        if row.cluster_id not in headlines:
            continue
        articles = await _fetch_articles_for_prompt(db, row.cluster_id)
        candidates_out.append({
            "clusterId": row.cluster_id,
            "headline": headlines[row.cluster_id],
            "sourcesAtPromotion": row.sources_at_promotion,
            "hoursToThreshold": row.hours_to_threshold,
            "promotedAt": _iso(row.promoted_at),
            "url": f"/api/v1/clusters/{row.cluster_id}",
            **_articles_out(articles),
        })

    refreshes_out = []
    for review in refreshes:
        story = refresh_stories.get(review.cluster_id)
        if review.cluster_id not in headlines or story is None:
            continue
        new_articles = await _fetch_articles_for_prompt(db, review.cluster_id, since=story.last_generated_at)
        refreshes_out.append({
            "id": review.id,
            "clusterId": review.cluster_id,
            "headline": headlines[review.cluster_id],
            "title": story.title,
            "sourceCount": review.source_count_at_review,
            "previousSourceCount": story.last_reviewed_source_count or story.sources_at_promotion,
            "createdAt": _iso(review.created_at),
            "lastGeneratedAt": _iso(story.last_generated_at),
            "url": f"/api/v1/clusters/{review.cluster_id}",
            "beats": [{
                "timeLabel": b.get("time_label", "?"),
                "label": b.get("label", ""),
                "narration": b.get("narration", ""),
            } for b in (story.beats or [])],
            **_articles_out(new_articles),
        })

    return {"live": live_out, "candidates": candidates_out, "refreshes": refreshes_out,
            "previewLimit": PREVIEW_LIMIT}


class DecideIn(BaseModel):
    action: Literal["approve", "reject"]


@router.post("/candidates/{cluster_id}", dependencies=[Depends(require_admin_write)])
async def decide_candidate(cluster_id: int, body: DecideIn, db: AsyncSession = Depends(get_db)):
    row = await db.scalar(
        select(BreakingStory).where(
            BreakingStory.cluster_id == cluster_id, BreakingStory.status == "pending_review"))
    if row is None:
        raise HTTPException(status_code=404, detail="This candidate is no longer waiting for review.")

    if body.action == "reject":
        row.status = "rejected"
        row.reviewed_at = utc_now()
        await db.commit()
        return {"ok": True, "clusterId": cluster_id, "status": "rejected", "beatCount": 0}

    # approve — this is the one LLM call in the whole redesigned flow's
    # candidate path. Uses extract_beats_only, not judge_and_extract_beats:
    # a human already made the developing-vs-echo call, so the prompt
    # doesn't ask the model to re-derive it (see NARRATIVE_ONLY_SYSTEM_PROMPT).
    # Still reads the full cluster — see breaking-human-review-plan.md for
    # why that part doesn't shrink.
    from app.services.breaking_narrative import BreakingNarrativeError, extract_beats_only

    articles = await _fetch_articles_for_prompt(db, cluster_id)
    if not articles:
        raise HTTPException(status_code=409, detail="This story has no articles to write a narrative from.")
    try:
        result = await extract_beats_only(articles)
    except BreakingNarrativeError as e:
        raise HTTPException(status_code=502, detail=f"The narrative pass failed, nothing was changed: {e}")

    row.reviewed_at = utc_now()
    if not result.get("beats"):
        # A human already said "yes, developing" — this is the model coming
        # back with nothing citable (rare). Treat as reject rather than
        # showing an empty Breaking card.
        row.status = "rejected"
    else:
        latest_beat_time = max(a["published_at"] for a in articles)
        row.status = "active"
        row.title = result.get("title") or None
        row.beats = result["beats"]
        row.last_beat_at = latest_beat_time
        row.last_generated_at = utc_now()
        row.last_generated_source_count = row.sources_at_promotion
        row.last_reviewed_source_count = row.sources_at_promotion
    await db.commit()
    return {"ok": True, "clusterId": cluster_id, "status": row.status,
            "title": row.title, "beatCount": len(row.beats or []) if row.status == "active" else 0}


@router.post("/refreshes/{review_id}", dependencies=[Depends(require_admin_write)])
async def decide_refresh(review_id: int, body: DecideIn, db: AsyncSession = Depends(get_db)):
    review = await db.get(BreakingRefreshReview, review_id)
    if review is None or review.status != "pending":
        raise HTTPException(status_code=404, detail="This refresh is no longer waiting for review.")
    story = await db.scalar(
        select(BreakingStory).where(BreakingStory.cluster_id == review.cluster_id))
    if story is None:
        raise HTTPException(status_code=404, detail="The breaking story for this refresh no longer exists.")

    def skip() -> dict:
        review.status = "rejected"
        review.reviewed_at = utc_now()
        story.last_reviewed_source_count = review.source_count_at_review
        return {"ok": True, "id": review_id, "status": "rejected", "newBeats": 0}

    if body.action == "reject":
        out = skip()
        await db.commit()
        return out

    # approve — the append-only LLM refresh pass. Uses extract_beats_only,
    # not judge_and_extract_beats: a human already confirmed this batch is
    # genuinely new by approving the review, so the prompt doesn't ask the
    # model to re-decide that (NARRATIVE_ONLY_REFRESH_SUFFIX). Already cheap
    # by design regardless (new articles + a short existing-beats summary,
    # not the full cluster).
    from app.services.breaking_narrative import BreakingNarrativeError, extract_beats_only

    new_articles = await _fetch_articles_for_prompt(db, review.cluster_id, since=story.last_generated_at)
    if not new_articles:
        out = skip()
        out["reason"] = "no_new_articles"
        await db.commit()
        return out

    try:
        result = await extract_beats_only(new_articles, existing_beats=story.beats or [])
    except BreakingNarrativeError as e:
        raise HTTPException(status_code=502, detail=f"The refresh pass failed, nothing was changed: {e}")

    added = result.get("beats", [])
    review.status = "approved"
    review.reviewed_at = utc_now()
    story.beats = (story.beats or []) + added
    story.last_generated_at = utc_now()
    story.last_generated_source_count = review.source_count_at_review
    story.last_reviewed_source_count = review.source_count_at_review
    if added:
        story.last_beat_at = max(a["published_at"] for a in new_articles)
    await db.commit()
    return {"ok": True, "id": review_id, "status": "approved", "newBeats": len(added)}


async def _invalidate_breaking_caches(cluster_id: int) -> None:
    # GET /breaking and GET /breaking/{cluster_id} each cache under these
    # keys (see app/main.py) — without this, a manual image pick/clear
    # wouldn't be visible in the app until CACHE_TTL_SECONDS expires. Same
    # fail-open convention as app/admin_timelines.py's
    # _invalidate_timeline_caches: caching is a perf optimization, never a
    # correctness dependency.
    try:
        client = get_redis_client()
        await client.delete("breaking:list")
        await client.delete(f"breaking:{cluster_id}")
    except Exception:
        pass


async def _gather_image_candidates(db: AsyncSession, cluster_id: int) -> list:
    """Every distinct-by-image_url article on this breaking story's cluster
    (unlike app/admin_timelines.py's version, there's no chain to scan — a
    BreakingStory is always exactly one cluster), sorted the same way the
    auto-selector ranks them (HD first, then most recent) so the best
    candidates surface first."""
    rows = (await db.execute(
        select(Article.image_url, Article.image_width, Article.image_height,
               Article.published_at, Source.name.label("source_name"))
        .outerjoin(Source, Source.id == Article.source_id)
        .where(Article.cluster_id == cluster_id, Article.image_url.isnot(None))
    )).all()
    seen_urls: set[str] = set()
    candidates = []
    for row in rows:
        if not row.image_url or row.image_url in seen_urls:
            continue
        seen_urls.add(row.image_url)
        candidates.append(row)
    candidates.sort(
        key=lambda a: (is_hd_image(a.image_width, a.image_height), a.published_at),
        reverse=True,
    )
    return candidates


async def _story_or_404(db: AsyncSession, cluster_id: int) -> BreakingStory:
    row = await db.scalar(select(BreakingStory).where(BreakingStory.cluster_id == cluster_id))
    if row is None:
        raise HTTPException(status_code=404, detail="There is no breaking story for that cluster.")
    return row


@router.get("/image/{cluster_id}")
async def image_picker(cluster_id: int, db: AsyncSession = Depends(get_db)):
    """Lets an admin override the auto-selected lead image (feed card + detail
    hero — see BreakingStory.manual_image_url) with any photo actually
    carried by an article on this story's cluster, rather than trusting the
    auto-pick every time."""
    row = await _story_or_404(db, cluster_id)
    headline = (await _headlines(db, {cluster_id})).get(cluster_id)
    candidates = await _gather_image_candidates(db, cluster_id)
    return {
        "clusterId": cluster_id,
        "title": row.title or headline or f"Cluster {cluster_id}",
        "status": row.status,
        "manualImageUrl": row.manual_image_url,
        "url": f"/api/v1/clusters/{cluster_id}",
        "images": [{
            "url": a.image_url,
            "sourceName": a.source_name or "Unknown",
            "publishedAt": _iso(a.published_at),
            "hd": is_hd_image(a.image_width, a.image_height),
            "current": a.image_url == row.manual_image_url,
        } for a in candidates],
    }


class ImageIn(BaseModel):
    imageUrl: str


@router.post("/image/{cluster_id}", dependencies=[Depends(require_admin_write)])
async def set_image(cluster_id: int, body: ImageIn, db: AsyncSession = Depends(get_db)):
    row = await _story_or_404(db, cluster_id)
    # Re-derive the candidate set server-side rather than trusting the
    # posted URL outright — it must be a real image already carried by some
    # article on this cluster, not an arbitrary string a client could be
    # made to submit.
    candidates = await _gather_image_candidates(db, cluster_id)
    if body.imageUrl not in {a.image_url for a in candidates}:
        raise HTTPException(status_code=400, detail="That image isn't carried by any article on this story.")

    row.manual_image_url = body.imageUrl
    row.updated_at = utc_now()
    await db.commit()
    await _invalidate_breaking_caches(cluster_id)
    return {"ok": True, "clusterId": cluster_id, "manualImageUrl": body.imageUrl}


@router.delete("/image/{cluster_id}", dependencies=[Depends(require_admin_write)])
async def clear_image(cluster_id: int, db: AsyncSession = Depends(get_db)):
    row = await _story_or_404(db, cluster_id)
    row.manual_image_url = None
    row.updated_at = utc_now()
    await db.commit()
    await _invalidate_breaking_caches(cluster_id)
    return {"ok": True, "clusterId": cluster_id, "manualImageUrl": None}
