"""Admin page for the Daily Brief: see the latest brief (stories, summaries,
audio) and rebuild one. Same session/CSRF/nav pattern as admin_timelines.py.
"""
from __future__ import annotations

import html
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

REBUILD_CONFIRM = (
    "Rebuild this morning's Daily Brief? This calls Claude and Sarvam (roughly Rs 8), takes a few "
    "minutes, and replaces the live brief only if the rebuild succeeds."
)
NOTICES = {
    "already-running": "A brief build is already running.",
    "not-configured": "Audio isn't configured on this server (SARVAM_API_KEY / Supabase storage); "
                      "a rebuild would publish text-only.",
}


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
        f"<h2>Brief for {row.brief_date:%A %d %B %Y} <span class=meta>({row.status})</span></h2>"
        f"<p class=meta>Generated {when}</p>{audio}"
        f"<p><b>Intro:</b> {html.escape(script.get('intro', ''))}</p>{stories}"
        f"<p><b>Closing:</b> {html.escape(script.get('closing', ''))}</p>")


@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request, notice: str = "", db: AsyncSession = Depends(get_db)):
    csrf = session_csrf(request)
    if not csrf:
        return RedirectResponse("/admin/daily-brief/login", status_code=303)

    today = datetime.now(IST).date()
    status = await daily_brief.get_status(today)
    running = status.get("state") == "running"
    row = (await db.execute(select(DailyBrief).order_by(desc(DailyBrief.brief_date)).limit(1))).scalar_one_or_none()

    if running:
        control = "<button disabled>Building&hellip;</button>"
    else:
        control = (
            f"<form method=post action='/admin/daily-brief/rebuild' "
            f"onsubmit=\"return confirm('{html.escape(REBUILD_CONFIRM, quote=True)}')\">"
            f"<input type=hidden name=csrf value='{html.escape(csrf)}'>"
            f"<button>{'Rebuild' if row and row.brief_date == today else 'Build'} today's brief</button></form>")
    run = _run_summary(status)
    refresh = "<script>setTimeout(function(){location.reload()},10000)</script>" if running else ""
    notice_html = f"<p class=danger>{html.escape(NOTICES[notice])}</p>" if notice in NOTICES else ""
    body = _brief_block(row) if row and row.items else "<p class=meta>No brief has been built yet.</p>"

    return layout(TITLE, (
        f"{notice_html}<h1>Daily Brief</h1>"
        f"<p class=meta>Built automatically at 05:00 IST from yesterday's stories. Today (IST) is {today}.</p>"
        + (f"<p class=meta>{run}</p>" if run else "")
        + f"{control}{body}{refresh}"), current="/admin/daily-brief")


@router.post("/rebuild")
async def rebuild(request: Request, background_tasks: BackgroundTasks):
    fields = await form_fields(request)
    verify(request, fields)
    if await daily_brief.in_progress():
        return RedirectResponse("/admin/daily-brief?notice=already-running", status_code=303)
    if not is_configured():
        return RedirectResponse("/admin/daily-brief?notice=not-configured", status_code=303)
    today = datetime.now(IST).date()
    # Marked running before the response so the redirected page already shows
    # it; the task itself takes the lease that makes a second click a no-op.
    await daily_brief.set_status(today, "running", "starting")
    background_tasks.add_task(daily_brief.run_build_task, today, force=True)
    return RedirectResponse("/admin/daily-brief", status_code=303)


@router.get("/status")
async def status_json(request: Request):
    """JSON status of today's build, for polling."""
    if not session_csrf(request):
        raise HTTPException(status_code=401, detail="Not signed in")
    today = datetime.now(IST).date()
    status = await daily_brief.get_status(today)
    running = await daily_brief.in_progress() or status.get("state") == "running"
    return JSONResponse({
        "brief_date": today.isoformat(),
        "state": "running" if running else status.get("state", "idle"),
        "message": status.get("message", ""),
    })
