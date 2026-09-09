"""Human-review queue for the "Breaking" slot — see
backend/docs/breaking-human-review-plan.md and app.services.breaking's
docstring. app.services.breaking only ever writes pending_review /
pending rows (pure SQL, no LLM call); THIS module is where a human's
approve decision actually triggers judge_and_extract_beats. A reject never
calls the LLM at all.

Same session/CSRF/nav/login plumbing as app/admin_timelines.py — one
admin, one login, several pages.
"""
from __future__ import annotations

import html

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_session import (
    credentials_match,
    form_fields,
    layout,
    login_form,
    nav,
    session_csrf,
    set_session_cookie,
    verify,
)
from app.database import get_db
from app.models import BreakingRefreshReview, BreakingStory, StoryCluster, utc_now
from app.services.breaking import _fetch_articles_for_prompt

router = APIRouter(prefix="/admin/breaking")
TITLE = "Breaking review"

# How many article rows to show per candidate/refresh — enough to sanity-
# check the judgement, not a re-read of the whole cluster. See the design
# discussion: this has to stay a fast glance.
PREVIEW_LIMIT = 25


@router.get("/login", response_class=HTMLResponse)
async def login_page():
    return login_form(TITLE, "/admin/breaking/login")


@router.post("/login")
async def login(request: Request):
    fields = await form_fields(request)
    if not credentials_match(fields):
        return layout(TITLE, "<h1>Sign in failed</h1><p class=danger>Invalid credentials.</p>"
                             "<a href='/admin/breaking/login'>Try again</a>")
    response = RedirectResponse("/admin/breaking", status_code=303)
    set_session_cookie(response, request)
    return response


def _article_lines(articles: list[dict]) -> str:
    shown = articles[:PREVIEW_LIMIT]
    lines = "".join(
        f"<li>{a['published_at']:%b %d, %H:%M} UTC &middot; "
        f"<b>{html.escape(a['source_name'])}</b> &middot; {html.escape(a['title'])}</li>"
        for a in shown
    )
    omitted = len(articles) - len(shown)
    more = f"<li class=meta>...and {omitted} more</li>" if omitted > 0 else ""
    return f"<ul>{lines}{more}</ul>"


def _candidate_card(row: BreakingStory, cluster: StoryCluster, articles: list[dict], csrf: str) -> str:
    return (
        f"<div class=task><h2>{html.escape(cluster.headline)}</h2>"
        f"<p class=meta>cluster {row.cluster_id} &middot; "
        f"{row.sources_at_promotion} sources &middot; "
        f"crossed the threshold {row.hours_to_threshold}h ago</p>"
        f"{_article_lines(articles)}"
        f"<form method=post action='/admin/breaking/candidate/{row.cluster_id}/decide'>"
        f"<input type=hidden name=csrf value='{html.escape(csrf)}'>"
        f"<button name=action value=approve>Genuinely developing &mdash; write narrative</button>"
        f"<button name=action value=reject>Just echo &mdash; reject</button></form></div>"
    )


def _refresh_card(review: BreakingRefreshReview, story: BreakingStory, cluster: StoryCluster,
                   new_articles: list[dict], csrf: str) -> str:
    existing = "".join(
        f"<li>[{b.get('time_label', '?')}] <b>{html.escape(b.get('label', ''))}</b> "
        f"&mdash; {html.escape(b.get('narration', ''))}</li>"
        for b in (story.beats or [])
    )
    return (
        f"<div class=task><h2>{html.escape(cluster.headline)}</h2>"
        f"<p class=meta>cluster {review.cluster_id} &middot; "
        f"{review.source_count_at_review} sources now, was "
        f"{story.last_reviewed_source_count or story.sources_at_promotion} at last review</p>"
        f"<p class=meta>Existing beats:</p><ul>{existing}</ul>"
        f"<p class=meta>New articles since:</p>"
        f"{_article_lines(new_articles)}"
        f"<form method=post action='/admin/breaking/refresh/{review.id}/decide'>"
        f"<input type=hidden name=csrf value='{html.escape(csrf)}'>"
        f"<button name=action value=approve>Genuinely new &mdash; extend narrative</button>"
        f"<button name=action value=reject>Just echo &mdash; skip</button></form></div>"
    )


@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    csrf = session_csrf(request)
    if not csrf:
        return RedirectResponse("/admin/breaking/login", status_code=303)

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
            select(BreakingStory).where(
                BreakingStory.cluster_id.in_([r.cluster_id for r in refreshes]))
        )).scalars().all()
        refresh_stories = {row.cluster_id: row for row in rows}

    cluster_ids = {row.cluster_id for row in candidates} | {row.cluster_id for row in refreshes}
    clusters = {}
    if cluster_ids:
        rows = (await db.execute(
            select(StoryCluster).where(StoryCluster.id.in_(cluster_ids))
        )).scalars().all()
        clusters = {row.id: row for row in rows}

    candidate_cards = []
    for row in candidates:
        cluster = clusters.get(row.cluster_id)
        if cluster is None:
            continue
        articles = await _fetch_articles_for_prompt(db, row.cluster_id)
        candidate_cards.append(_candidate_card(row, cluster, articles, csrf))

    refresh_cards = []
    for review in refreshes:
        cluster = clusters.get(review.cluster_id)
        story = refresh_stories.get(review.cluster_id)
        if cluster is None or story is None:
            continue
        new_articles = await _fetch_articles_for_prompt(db, review.cluster_id, since=story.last_generated_at)
        refresh_cards.append(_refresh_card(review, story, cluster, new_articles, csrf))

    body = (
        f"<h1>Breaking review</h1>{nav('/admin/breaking')}"
        f"<p class=meta>Approving runs the LLM narrative pass; rejecting costs nothing. "
        f"See backend/docs/breaking-human-review-plan.md.</p>"
        f"<h2>New candidates ({len(candidate_cards)})</h2>"
        + ("".join(candidate_cards) if candidate_cards else "<p class=meta>None waiting.</p>")
        + f"<h2>Refreshes ({len(refresh_cards)})</h2>"
        + ("".join(refresh_cards) if refresh_cards else "<p class=meta>None waiting.</p>")
    )
    return layout(TITLE, body)


@router.post("/candidate/{cluster_id}/decide")
async def decide_candidate(cluster_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    fields = await form_fields(request)
    verify(request, fields)
    action = fields.get("action")
    if action not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="Invalid action")

    row = await db.scalar(
        select(BreakingStory).where(
            BreakingStory.cluster_id == cluster_id, BreakingStory.status == "pending_review"))
    if row is None:
        raise HTTPException(status_code=404, detail="No pending candidate for that cluster")

    if action == "reject":
        row.status = "rejected"
        row.reviewed_at = utc_now()
        await db.commit()
        return RedirectResponse("/admin/breaking", status_code=303)

    # approve — this is the one LLM call in the whole redesigned flow's
    # candidate path. Uses extract_beats_only, not judge_and_extract_beats:
    # a human already made the developing-vs-echo call, so the prompt
    # doesn't ask the model to re-derive it (see NARRATIVE_ONLY_SYSTEM_PROMPT).
    # Still reads the full cluster — see breaking-human-review-plan.md for
    # why that part doesn't shrink.
    from app.services.breaking_narrative import BreakingNarrativeError, extract_beats_only

    articles = await _fetch_articles_for_prompt(db, cluster_id)
    if not articles:
        raise HTTPException(status_code=409, detail="Cluster has no articles")
    try:
        result = await extract_beats_only(articles)
    except BreakingNarrativeError as e:
        raise HTTPException(status_code=502, detail=f"LLM narrative pass failed: {e}")

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
    return RedirectResponse("/admin/breaking", status_code=303)


@router.post("/refresh/{review_id}/decide")
async def decide_refresh(review_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    fields = await form_fields(request)
    verify(request, fields)
    action = fields.get("action")
    if action not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="Invalid action")

    review = await db.get(BreakingRefreshReview, review_id)
    if review is None or review.status != "pending":
        raise HTTPException(status_code=404, detail="No pending refresh review with that id")
    story = await db.scalar(
        select(BreakingStory).where(BreakingStory.cluster_id == review.cluster_id))
    if story is None:
        raise HTTPException(status_code=404, detail="No breaking_stories row for that cluster")

    if action == "reject":
        review.status = "rejected"
        review.reviewed_at = utc_now()
        story.last_reviewed_source_count = review.source_count_at_review
        await db.commit()
        return RedirectResponse("/admin/breaking", status_code=303)

    # approve — the append-only LLM refresh pass. Uses extract_beats_only,
    # not judge_and_extract_beats: a human already confirmed this batch is
    # genuinely new by approving the review, so the prompt doesn't ask the
    # model to re-decide that (NARRATIVE_ONLY_REFRESH_SUFFIX). Already cheap
    # by design regardless (new articles + a short existing-beats summary,
    # not the full cluster).
    from app.services.breaking_narrative import BreakingNarrativeError, extract_beats_only

    new_articles = await _fetch_articles_for_prompt(db, review.cluster_id, since=story.last_generated_at)
    if not new_articles:
        review.status = "rejected"
        review.reviewed_at = utc_now()
        story.last_reviewed_source_count = review.source_count_at_review
        await db.commit()
        return RedirectResponse("/admin/breaking", status_code=303)

    try:
        result = await extract_beats_only(new_articles, existing_beats=story.beats or [])
    except BreakingNarrativeError as e:
        raise HTTPException(status_code=502, detail=f"LLM refresh pass failed: {e}")

    review.status = "approved"
    review.reviewed_at = utc_now()
    story.beats = (story.beats or []) + result.get("beats", [])
    story.last_generated_at = utc_now()
    story.last_generated_source_count = review.source_count_at_review
    story.last_reviewed_source_count = review.source_count_at_review
    if result.get("beats"):
        story.last_beat_at = max(a["published_at"] for a in new_articles)
    await db.commit()
    return RedirectResponse("/admin/breaking", status_code=303)
