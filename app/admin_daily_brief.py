"""Admin page for the Daily Brief and the Late-Night Wrap-up: see the latest
of each kind (stories, summaries, audio) and rebuild either. Same
session/CSRF/nav pattern as admin_timelines.py.
"""
from __future__ import annotations

import html
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

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
from app.models import DailyBrief
from app.services import daily_brief
from app.services.timeline_audio import is_configured

router = APIRouter(prefix="/admin/daily-brief")
TITLE = "Daily Brief"
IST = ZoneInfo("Asia/Kolkata")

KINDS = {"brief": "Daily Brief", "wrapup": "Late-Night Wrap-up"}

# Rendered through json.dumps() into the onsubmit handler (see the form below),
# so any quote character is safe. It used to be html.escape()d into a single-
# quoted JS string, where the apostrophes in "morning's"/"tonight's" ended the
# string early: the handler was a syntax error, the confirm never appeared, and
# the form submitted (a ~Rs 8 rebuild) on a single click.
REBUILD_CONFIRM = {
    "brief": "Rebuild the morning Daily Brief? This calls Claude and Sarvam (roughly Rs 8) and takes a few "
             "minutes. When it finishes it replaces the live brief; if voicing fails it publishes text-only "
             "and the live brief loses its audio.",
    "wrapup": "Rebuild the Late-Night Wrap-up? This calls Claude and Sarvam (roughly Rs 8) and takes a few "
              "minutes. When it finishes it replaces the live wrap-up; if voicing fails it publishes "
              "text-only and the live wrap-up loses its audio.",
}
NOTICES = {
    "already-running": "A build of this kind is already running.",
    "not-configured": "Audio isn't configured on this server (SARVAM_API_KEY / Supabase storage); "
                      "rebuilding is disabled until it is configured.",
}


def _kind(request: Request) -> str:
    kind = request.query_params.get("kind", "brief")
    return kind if kind in KINDS else "brief"


@router.get("/login", response_class=HTMLResponse)
async def login_page():
    return login_form(TITLE, "/admin/daily-brief/login")


@router.post("/login")
async def login(request: Request):
    fields = await form_fields(request)
    if not credentials_match(fields):
        return layout(TITLE, "<h1>Sign in failed</h1><p class=danger>Invalid credentials.</p>"
                             "<a href='/admin/daily-brief/login'>Try again</a>")
    response = RedirectResponse("/admin/daily-brief", status_code=303)
    set_session_cookie(response, request)
    return response


def _run_summary(status: dict) -> str:
    if not status:
        return ""
    state = status.get("state")
    message = html.escape(status.get("message") or "")
    at = (status.get("at") or "")[:16].replace("T", " ")
    if state == "running":
        return f"<b>Building&hellip;</b> {message} (started {html.escape(at)} UTC). The page refreshes itself."
    if state == "failed":
        return f"<b class=danger>Last run failed:</b> {message} <span class=meta>({html.escape(at)} UTC)</span>"
    return f"<span class=meta>Last run: {message} ({html.escape(at)} UTC)</span>"


def _brief_block(row: DailyBrief) -> str:
    seconds = row.audio_duration_seconds or 0
    audio = (
        f"<audio controls preload=none src='{html.escape(row.audio_url, quote=True)}'></audio>"
        f"<p class=meta>{seconds // 60}:{seconds % 60:02d}</p>"
        if row.audio_url else "<p class=meta><b>Text-only</b> — no audio on this brief.</p>")
    script = row.script or {}
    spoken = {i["cluster_id"]: i["spoken"] for i in script.get("items", [])}
    stories = "".join(
        f"<div class=task><h2>{n}. {html.escape(item['headline'])}</h2>"
        f"<p class=meta>{html.escape(item['category'])} &middot; {item['source_count']} outlets &middot; "
        f"{'most covered' if item.get('slot_kind') == 'most_covered' else 'category pick'} &middot; "
        f"cluster <a target=_blank href='/api/v1/clusters/{item['cluster_id']}'>{item['cluster_id']}</a>"
        + (f" &middot; starts at {item['audio_offset']:.0f}s" if item.get("audio_offset") is not None else "")
        + f"</p><p><b>On screen:</b> {html.escape(item['summary'])}</p>"
        f"<p class=meta><b>Spoken:</b> {html.escape(spoken.get(item['cluster_id'], ''))}</p></div>"
        for n, item in enumerate(row.items or [], start=1))
    when = f"{row.generated_at:%Y-%m-%d %H:%M} UTC" if row.generated_at else "an unknown time"
    return (
        f"<h2>{KINDS[row.kind]} for {row.brief_date:%A %d %B %Y} <span class=meta>({row.status})</span></h2>"
        f"<p class=meta>Generated {when}</p>{audio}"
        f"<p><b>Intro:</b> {html.escape(script.get('intro', ''))}</p>{stories}"
        f"<p><b>Closing:</b> {html.escape(script.get('closing', ''))}</p>")


def _kind_tabs(active: str) -> str:
    return "".join(
        f"<a href='/admin/daily-brief?kind={k}' style='margin-right:12px'>"
        f"{'<b>' if k == active else ''}{label}{'</b>' if k == active else ''}</a>"
        for k, label in KINDS.items()
    )


@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request, notice: str = "", db: AsyncSession = Depends(get_db)):
    csrf = session_csrf(request)
    if not csrf:
        return RedirectResponse("/admin/daily-brief/login", status_code=303)

    kind = _kind(request)
    today = datetime.now(IST).date()
    status = await daily_brief.get_status(today, kind)
    running = status.get("state") == "running"
    row = (
        await db.execute(
            select(DailyBrief).where(DailyBrief.kind == kind).order_by(desc(DailyBrief.brief_date)).limit(1)
        )
    ).scalar_one_or_none()

    if running:
        control = "<button disabled>Building&hellip;</button>"
    else:
        control = (
            f"<form method=post action='/admin/daily-brief/rebuild' "
            f"onsubmit=\"return confirm({html.escape(json.dumps(REBUILD_CONFIRM[kind]), quote=True)})\">"
            f"<input type=hidden name=csrf value='{html.escape(csrf)}'>"
            f"<input type=hidden name=kind value='{kind}'>"
            f"<button>{'Rebuild' if row and row.brief_date == today else 'Build'} {KINDS[kind].lower()}</button></form>")
    run = _run_summary(status)
    refresh = f"<script>setTimeout(function(){{location.reload()}},10000)</script>" if running else ""
    notice_html = f"<p class=danger>{html.escape(NOTICES[notice])}</p>" if notice in NOTICES else ""
    body = _brief_block(row) if row and row.items else f"<p class=meta>No {KINDS[kind].lower()} has been built yet.</p>"

    return layout(TITLE, (
        f"{notice_html}<h1>Daily Brief</h1>{_kind_tabs(kind)}"
        f"<p class=meta>Daily Brief built automatically at 05:00 IST from yesterday's stories; "
        f"Late-Night Wrap-up at 19:30 IST from today's stories up to 7 PM. Today (IST) is {today}.</p>"
        + (f"<p class=meta>{run}</p>" if run else "")
        + f"{control}{body}{refresh}"), current="/admin/daily-brief")


@router.post("/rebuild")
async def rebuild(request: Request, background_tasks: BackgroundTasks):
    fields = await form_fields(request)
    verify(request, fields)
    kind = fields.get("kind", "brief")
    kind = kind if kind in KINDS else "brief"
    if await daily_brief.in_progress(kind):
        return RedirectResponse(f"/admin/daily-brief?kind={kind}&notice=already-running", status_code=303)
    if not is_configured():
        return RedirectResponse(f"/admin/daily-brief?kind={kind}&notice=not-configured", status_code=303)
    today = datetime.now(IST).date()
    # Marked running before the response so the redirected page already shows
    # it; the task itself takes the lease that makes a second click a no-op.
    await daily_brief.set_status(today, kind, "running", "starting")
    background_tasks.add_task(daily_brief.run_build_task, today, kind, force=True)
    return RedirectResponse(f"/admin/daily-brief?kind={kind}", status_code=303)


@router.get("/status")
async def status_json(request: Request):
    """JSON status of today's build, for polling."""
    if not session_csrf(request):
        raise HTTPException(status_code=401, detail="Not signed in")
    kind = _kind(request)
    today = datetime.now(IST).date()
    status = await daily_brief.get_status(today, kind)
    running = await daily_brief.in_progress(kind) or status.get("state") == "running"
    return JSONResponse({
        "brief_date": today.isoformat(),
        "kind": kind,
        "state": "running" if running else status.get("state", "idle"),
        "message": status.get("message", ""),
    })
