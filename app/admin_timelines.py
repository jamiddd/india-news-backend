"""Browsable UI for the Timeline/Context tab's editorial picks.

Until now this human-intervention step (see StoryTimelineFeature's docstring)
only existed as bare JSON endpoints in main.py — GET /admin/timelines/picks,
POST /admin/timelines/pick, POST /admin/timelines/unpick — cookie-gated but
with no page to click through and no cluster search, so picking a story
meant already knowing its cluster_id and hand-rolling a curl/Postman call.
This wraps those endpoints in the same session/CSRF/nav pattern as the rest
of the admin, plus a headline search to find a cluster_id in the first place.
"""
from __future__ import annotations

import html

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import desc, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.admin_session import (
    credentials_match,
    form_fields,
    layout,
    login_form,
    session_csrf,
    set_session_cookie,
    verify,
)
from app.database import get_db
from app.models import Article, StoryCluster, StoryTimelineFeature, utc_now
from app.redis_client import get_redis_client
from app.services import timeline_narration as narration
from app.services.image_extractor import is_hd_image
from app.services.timeline_audio import SCRIPT_VERSION, is_configured

router = APIRouter(prefix="/admin/timelines")
TITLE = "Timelines"

SEARCH_LIMIT = 15


@router.get("/login", response_class=HTMLResponse)
async def login_page():
    return login_form(TITLE, "/admin/timelines/login")


@router.post("/login")
async def login(request: Request):
    fields = await form_fields(request)
    if not credentials_match(fields):
        return layout(TITLE, "<h1>Sign in failed</h1><p class=danger>Invalid credentials.</p>"
                             "<a href='/admin/timelines/login'>Try again</a>")
    response = RedirectResponse("/admin/timelines", status_code=303)
    set_session_cookie(response, request)
    return response


NARRATE_CONFIRM = (
    "Generate narration for this timeline? This calls Claude and Sarvam (roughly Rs 15-20 for a typical "
    "story), takes a few minutes, and replaces its current audio only if it succeeds."
)
NOTICES = {
    "already-running": "That timeline is already being narrated.",
    "not-eligible": "That timeline can't be narrated (it isn't a coherent timeline with written beats yet).",
    "not-configured": "Narration isn't configured on this server (SARVAM_API_KEY / Supabase storage).",
}


def _audio_summary(row: StoryTimelineFeature) -> str:
    if not row.audio_url:
        return "<span class=meta>No narration audio yet.</span>"
    seconds = row.audio_duration_seconds or 0
    when = f"{row.audio_generated_at:%Y-%m-%d %H:%M} UTC" if row.audio_generated_at else "an unknown time"
    current = (row.spoken_script or {}).get("version") == SCRIPT_VERSION
    kind = "<b class=done>Current narration</b>" if current else "<b>Older narration (previous voice)</b>"
    return f"{kind} &middot; {seconds // 60}:{seconds % 60:02d} &middot; generated {when}"


def _run_summary(status: dict | None) -> str:
    if not status:
        return ""
    state = status.get("state")
    message = html.escape(status.get("message") or "")
    at = (status.get("at") or "")[:16].replace("T", " ")
    if state == "running":
        return f"<b>Generating&hellip;</b> started {html.escape(at)} UTC. This takes a few minutes; the page refreshes itself."
    if state == "failed":
        return f"<b class=danger>Last run failed:</b> {message} <span class=meta>({html.escape(at)} UTC)</span>"
    return f"<span class=meta>Last run: {message} ({html.escape(at)} UTC)</span>"


def _narration_block(row: StoryTimelineFeature, csrf: str, status: dict | None, configured: bool) -> str:
    running = bool(status) and status.get("state") == "running"
    blocked = narration.can_narrate(row)
    if running:
        control = "<button disabled>Generating&hellip;</button>"
    elif blocked:
        control = f"<button disabled>Generate narration</button> <span class=meta>({html.escape(blocked)})</span>"
    elif not configured:
        control = "<button disabled>Generate narration</button> <span class=meta>(not configured on this server)</span>"
    else:
        label = "Regenerate narration" if row.audio_url else "Generate narration"
        control = (
            f"<form method=post action='/admin/timelines/narrate' data-busy-msg='Starting&hellip;' "
            f"onsubmit=\"return confirm('{html.escape(NARRATE_CONFIRM, quote=True)}')\">"
            f"<input type=hidden name=csrf value='{html.escape(csrf)}'>"
            f"<input type=hidden name=row_id value='{row.id}'>"
            f"<button>{label}</button></form>")
    run = _run_summary(status)
    return (
        f"<p class=meta>Narration: {_audio_summary(row)}</p>"
        + (f"<p class=meta>{run}</p>" if run else "")
        + control)


def _pick_row(row: StoryTimelineFeature, csrf: str, status: dict | None = None, configured: bool = True) -> str:
    label = html.escape(row.title or row.anchor_label or f"Cluster {row.anchor_cluster_id}")
    flags = []
    if row.is_editorial_pick:
        flags.append("<b class=done>Editorial pick</b>")
    else:
        flags.append("<span class=meta>Algorithmic slot</span>")
    if not row.last_seen_in_top:
        flags.append("<span class=meta>not currently in the top 5</span>")
    if row.coherent is False:
        flags.append("<b class=danger>chain stopped cohering</b>")
    if row.narrative_generated_at is None:
        flags.append("<span class=meta>narrative not generated yet</span>")

    action = "unpick" if row.is_editorial_pick else "pick"
    button = (
        f"<button name=action value={action}>"
        f"{'Remove editorial pick' if action == 'unpick' else 'Make editorial pick'}</button>")

    image_status = (
        "<b class=done>manual image override</b>" if row.manual_image_url
        else "<span class=meta>image: auto-selected</span>")

    return (
        f"<div class=task id='row-{row.id}'><h2>{label}</h2>"
        f"<p class=meta>{' · '.join(flags)}</p>"
        f"<p class=meta>anchor cluster "
        f"<a target=_blank href='/api/v1/clusters/{row.anchor_cluster_id}'>{row.anchor_cluster_id}</a>"
        f" · picked {row.picked_at:%Y-%m-%d %H:%M} UTC</p>"
        f"<p class=meta>{image_status} &middot; "
        f"<a href='/admin/timelines/image/{row.id}'>Choose image&hellip;</a></p>"
        f"<form method=post action='/admin/timelines/update'>"
        f"<input type=hidden name=csrf value='{html.escape(csrf)}'>"
        f"<input type=hidden name=cluster_id value='{row.anchor_cluster_id}'>"
        f"<input type=hidden name=action value='{action}'>{button}</form>"
        f"{_narration_block(row, csrf, status, configured)}</div>")


def _search_result(cluster: StoryCluster, csrf: str) -> str:
    return (
        f"<div class=task><h2>{html.escape(cluster.headline)}</h2>"
        f"<p class=meta>cluster {cluster.id} · {cluster.distinct_source_count} sources</p>"
        f"<form method=post action='/admin/timelines/update'>"
        f"<input type=hidden name=csrf value='{html.escape(csrf)}'>"
        f"<input type=hidden name=cluster_id value='{cluster.id}'>"
        f"<input type=hidden name=action value='pick'>"
        f"<button>Make editorial pick</button></form></div>")


@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request, q: str = "", notice: str = "", db: AsyncSession = Depends(get_db)):
    csrf = session_csrf(request)
    if not csrf:
        return RedirectResponse("/admin/timelines/login", status_code=303)
    q = q.strip()

    picks = (await db.execute(
        select(StoryTimelineFeature).order_by(
            desc(StoryTimelineFeature.is_editorial_pick), desc(StoryTimelineFeature.picked_at))
    )).scalars().all()
    picked_ids = {row.anchor_cluster_id for row in picks}

    search_html = ""
    if q:
        clusters = (await db.execute(
            select(StoryCluster).where(StoryCluster.headline.ilike(f"%{q}%"))
            .order_by(desc(StoryCluster.last_updated_at)).limit(SEARCH_LIMIT)
        )).scalars().all()
        results = [c for c in clusters if c.id not in picked_ids]
        search_html = (
            f"<h2>Search results</h2>"
            + ("".join(_search_result(c, csrf) for c in results) if results
               else "<p class=meta>No unpicked clusters match.</p>"))

    statuses = await narration.get_statuses(row.id for row in picks)
    configured = is_configured()
    picks_html = (
        "".join(_pick_row(row, csrf, statuses.get(row.id), configured) for row in picks)
        if picks else "<p class=meta>No timeline rows yet.</p>")
    any_running = any(status.get("state") == "running" for status in statuses.values())
    # A run is started on one worker/server and finishes minutes later, so
    # while any is in flight the page re-polls itself (statuses are shared).
    refresh = "<script>setTimeout(function(){location.reload()},15000)</script>" if any_running else ""
    notice_html = f"<p class=danger>{html.escape(NOTICES[notice])}</p>" if notice in NOTICES else ""

    return layout(TITLE, (
        f"{notice_html}"
        f"<h1>Timeline editorial picks</h1>"
        f"<p class=meta>Up to 5 slots show in the Timeline/Context tab; editorial picks fill first, "
        f"the generation script fills the rest by chain length &times; recency.</p>"
        f"<form method=get><input name=q placeholder='search by headline' "
        f"value='{html.escape(q, quote=True)}'><button>Search</button></form>"
        f"{search_html}<h2>Current rows</h2>{picks_html}{refresh}"), current="/admin/timelines")


@router.post("/update")
async def update(request: Request, db: AsyncSession = Depends(get_db)):
    fields = await form_fields(request)
    verify(request, fields)

    action = fields.get("action")
    if action not in ("pick", "unpick"):
        raise HTTPException(status_code=400, detail="Invalid action")
    cluster_id = int(fields["cluster_id"])

    if action == "pick":
        cluster = await db.get(StoryCluster, cluster_id)
        if cluster is None:
            raise HTTPException(status_code=404, detail="Cluster not found")
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
            raise HTTPException(status_code=404, detail="No pick found for that cluster")
        row.is_editorial_pick = False
        row.updated_at = utc_now()

    await db.commit()
    return RedirectResponse("/admin/timelines", status_code=303)


@router.post("/narrate")
async def narrate(request: Request, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    """Start narrating one timeline (Claude writes the spoken script, Sarvam
    voices it, the row is updated). Returns at once — the work takes minutes,
    so it runs as a background task and the page shows its status."""
    fields = await form_fields(request)
    verify(request, fields)
    try:
        row_id = int(fields["row_id"])
    except (KeyError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid row id")
    row = await db.get(StoryTimelineFeature, row_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Timeline not found")

    if narration.can_narrate(row):
        return RedirectResponse("/admin/timelines?notice=not-eligible", status_code=303)
    if not is_configured():
        return RedirectResponse("/admin/timelines?notice=not-configured", status_code=303)
    if await narration.in_progress(row_id):
        return RedirectResponse("/admin/timelines?notice=already-running", status_code=303)

    # Marked running before the response so the redirected page already shows
    # it; the task itself takes the per-row lease that makes a second click a no-op.
    await narration.set_status(row_id, "running")
    background_tasks.add_task(narration.narrate_row, row_id)
    return RedirectResponse(f"/admin/timelines#row-{row_id}", status_code=303)


@router.get("/narrate/{row_id}")
async def narration_status(row_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    """JSON status of a narration run, for polling."""
    if not session_csrf(request):
        raise HTTPException(status_code=401, detail="Not signed in")
    row = await db.get(StoryTimelineFeature, row_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Timeline not found")
    status = (await narration.get_statuses([row_id])).get(row_id) or {}
    running = await narration.in_progress(row_id) or status.get("state") == "running"
    return JSONResponse({
        "row_id": row_id,
        "state": "running" if running else status.get("state", "idle"),
        "message": status.get("message", ""),
        "has_audio": bool(row.audio_url),
        "audio_is_current": (row.spoken_script or {}).get("version") == SCRIPT_VERSION and bool(row.audio_url),
        "audio_generated_at": row.audio_generated_at.isoformat() if row.audio_generated_at else None,
    })


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


def _image_option(article: Article, row: StoryTimelineFeature, csrf: str) -> str:
    is_current = article.image_url == row.manual_image_url
    hd_badge = " <b class=done>HD</b>" if is_hd_image(article.image_width, article.image_height) else ""
    source_name = html.escape(article.source.name if article.source else "Unknown")
    when = f"{article.published_at:%Y-%m-%d %H:%M} UTC"
    image_url = html.escape(article.image_url, quote=True)
    action = (
        "<b class=done>Currently selected</b>" if is_current else
        f"<form method=post action='/admin/timelines/image/set'>"
        f"<input type=hidden name=csrf value='{html.escape(csrf)}'>"
        f"<input type=hidden name=row_id value='{row.id}'>"
        f"<input type=hidden name=image_url value='{image_url}'>"
        f"<button>Use this image</button></form>"
    )
    return (
        f"<div style='display:inline-block;width:200px;margin:0 12px 20px 0;vertical-align:top'>"
        f"<img src='{image_url}' loading=lazy alt='' "
        f"style='width:200px;height:120px;object-fit:cover;border-radius:8px;display:block;background:var(--surface)'>"
        f"<p class=meta style='margin:6px 0 2px'>{source_name}{hd_badge}</p>"
        f"<p class=meta style='margin:0 0 6px'>{when}</p>"
        f"{action}</div>")


@router.get("/image/{row_id}", response_class=HTMLResponse)
async def image_picker(row_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    """Lets an admin override the auto-selected lead image (list feed hero
    + detail cover art — see StoryTimelineFeature.manual_image_url) with
    any photo actually carried by an article somewhere in this timeline's
    chain, rather than trusting the HD-then-recency auto-pick every time."""
    csrf = session_csrf(request)
    if not csrf:
        return RedirectResponse("/admin/timelines/login", status_code=303)
    row = await db.get(StoryTimelineFeature, row_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Timeline not found")

    label = html.escape(row.title or row.anchor_label or f"Cluster {row.anchor_cluster_id}")
    candidates = await _gather_image_candidates(db, row)

    if row.manual_image_url:
        status_html = (
            f"<p><b class=done>Manual override active</b></p>"
            f"<form method=post action='/admin/timelines/image/clear'>"
            f"<input type=hidden name=csrf value='{html.escape(csrf)}'>"
            f"<input type=hidden name=row_id value='{row.id}'>"
            f"<button>Clear override (use auto-select)</button></form>")
    else:
        status_html = "<p class=meta>No override set — currently auto-selecting (HD first, then most recent).</p>"

    grid_html = (
        "".join(_image_option(a, row, csrf) for a in candidates) if candidates
        else "<p class=meta>No article images found across this timeline's chain.</p>")

    return layout(TITLE, (
        f"<p><a href='/admin/timelines'>&larr; Back to timelines</a></p>"
        f"<h1>Choose image &mdash; {label}</h1>"
        f"{status_html}"
        f"<h2>Available images ({len(candidates)})</h2>"
        f"<div>{grid_html}</div>"), current="/admin/timelines")


@router.post("/image/set")
async def set_image(request: Request, db: AsyncSession = Depends(get_db)):
    fields = await form_fields(request)
    verify(request, fields)
    try:
        row_id = int(fields["row_id"])
    except (KeyError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid row id")
    image_url = fields.get("image_url", "")

    row = await db.get(StoryTimelineFeature, row_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Timeline not found")

    # Re-derive the candidate set server-side rather than trusting the
    # posted URL outright — it must be a real image already carried by some
    # article in this chain, not an arbitrary string a form could be made
    # to submit.
    candidates = await _gather_image_candidates(db, row)
    if image_url not in {a.image_url for a in candidates}:
        raise HTTPException(status_code=400, detail="Not a valid image for this timeline")

    row.manual_image_url = image_url
    row.updated_at = utc_now()
    await db.commit()
    await _invalidate_timeline_caches(row_id)
    return RedirectResponse(f"/admin/timelines/image/{row_id}", status_code=303)


@router.post("/image/clear")
async def clear_image(request: Request, db: AsyncSession = Depends(get_db)):
    fields = await form_fields(request)
    verify(request, fields)
    try:
        row_id = int(fields["row_id"])
    except (KeyError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid row id")

    row = await db.get(StoryTimelineFeature, row_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Timeline not found")

    row.manual_image_url = None
    row.updated_at = utc_now()
    await db.commit()
    await _invalidate_timeline_caches(row_id)
    return RedirectResponse(f"/admin/timelines/image/{row_id}", status_code=303)
