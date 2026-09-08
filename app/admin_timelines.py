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

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import desc, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
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
from app.models import StoryCluster, StoryTimelineFeature, utc_now

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


def _pick_row(row: StoryTimelineFeature, csrf: str) -> str:
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

    return (
        f"<div class=task><h2>{label}</h2>"
        f"<p class=meta>{' · '.join(flags)}</p>"
        f"<p class=meta>anchor cluster "
        f"<a target=_blank href='/api/v1/clusters/{row.anchor_cluster_id}'>{row.anchor_cluster_id}</a>"
        f" · picked {row.picked_at:%Y-%m-%d %H:%M} UTC</p>"
        f"<form method=post action='/admin/timelines/update'>"
        f"<input type=hidden name=csrf value='{html.escape(csrf)}'>"
        f"<input type=hidden name=cluster_id value='{row.anchor_cluster_id}'>"
        f"<input type=hidden name=action value='{action}'>{button}</form></div>")


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
async def dashboard(request: Request, q: str = "", db: AsyncSession = Depends(get_db)):
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

    picks_html = "".join(_pick_row(row, csrf) for row in picks) if picks else "<p class=meta>No timeline rows yet.</p>"

    return layout(TITLE, (
        f"<h1>Timeline editorial picks</h1>{nav('/admin/timelines')}"
        f"<p class=meta>Up to 5 slots show in the Timeline/Context tab; editorial picks fill first, "
        f"the generation script fills the rest by chain length &times; recency.</p>"
        f"<form method=get><input name=q placeholder='search by headline' "
        f"value='{html.escape(q, quote=True)}'><button>Search</button></form>"
        f"{search_html}<h2>Current rows</h2>{picks_html}"))


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
