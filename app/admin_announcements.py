"""Admin control for the top-of-app banner — see app/models.py's
Announcement and the public GET /announcements/active endpoint in
app/main.py. Shows events, special offers or extra info at the top of the
app for a scheduled window, without a client release.

Same session/CSRF/nav/login plumbing as app/admin_topics.py.
"""
from __future__ import annotations

import html
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends
from fastapi.responses import HTMLResponse, RedirectResponse

from app.admin_session import (
    credentials_match,
    custom_select,
    form_fields,
    layout,
    login_form,
    session_csrf,
    set_session_cookie,
    verify,
)
from app.database import get_db
from app.models import Announcement, utc_now
from app.redis_client import get_redis_client

KINDS = ("event", "offer", "info")
ACTION_TYPES = ("", "url", "story", "paywall")


async def _invalidate_active_cache() -> None:
    # GET /announcements/active caches under this key (see app/main.py) —
    # without this, an admin's add/delete wouldn't be visible in the app
    # until the short cache TTL expires. Same fail-open-on-error convention
    # as _cache_get/_cache_set: caching is a perf optimization, never a
    # correctness dependency.
    try:
        await get_redis_client().delete("announcements:active")
    except Exception:
        pass

router = APIRouter(prefix="/admin/announcements")
TITLE = "Announcements"


def _parse_local_dt(raw: str, field: str) -> datetime:
    # <input type=datetime-local> posts "YYYY-MM-DDTHH:MM" with no
    # timezone. Treated as UTC — matches Announcement.starts_at/ends_at's
    # DateTime(timezone=True) columns.
    try:
        return datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{field} must be YYYY-MM-DDTHH:MM")


@router.get("/login", response_class=HTMLResponse)
async def login_page():
    return login_form(TITLE, "/admin/announcements/login")


@router.post("/login")
async def login(request: Request):
    fields = await form_fields(request)
    if not credentials_match(fields):
        return layout(TITLE, "<h1>Sign in failed</h1><p class=danger>Invalid credentials.</p>"
                             "<a href='/admin/announcements/login'>Try again</a>")
    response = RedirectResponse("/admin/announcements", status_code=303)
    set_session_cookie(response, request)
    return response


def _row(row: Announcement, csrf: str) -> str:
    active = row.starts_at <= utc_now() < row.ends_at
    status = "<b>active</b>" if active else ("upcoming" if row.starts_at > utc_now() else "expired")
    return (
        f"<tr><td>{row.kind}</td>"
        f"<td>{html.escape(row.title)}</td>"
        f"<td>{row.starts_at.isoformat()}</td>"
        f"<td>{row.ends_at.isoformat()}</td>"
        f"<td>{row.priority}</td>"
        f"<td>{status}</td>"
        f"<td><form method=post action='/admin/announcements/{row.id}/delete'>"
        f"<input type=hidden name=csrf value='{html.escape(csrf)}'>"
        f"<button class=danger>Delete</button></form></td></tr>"
    )


@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    csrf = session_csrf(request)
    if not csrf:
        return RedirectResponse("/admin/announcements/login", status_code=303)

    rows = (await db.execute(
        select(Announcement).where(Announcement.ends_at >= utc_now())
        .order_by(Announcement.starts_at, Announcement.priority.desc())
    )).scalars().all()

    table = (
        "<table><tr><th>Kind</th><th>Title</th><th>Starts</th><th>Ends</th>"
        "<th>Priority</th><th>Status</th><th></th></tr>"
        + "".join(_row(r, csrf) for r in rows)
        + "</table>"
        if rows else "<p class=meta>No announcements scheduled from now onward.</p>"
    )

    kind_select = custom_select("kind", [(k, k) for k in KINDS])
    action_select = custom_select(
        "action_type", [(a, a or "(none)") for a in ACTION_TYPES]
    )

    body = (
        f"<h1>Announcements</h1>"
        f"<p class=meta>Shown one at a time at the top of the app (highest priority first), "
        f"queued while active. Auto-expires at End — nothing to clean up.</p>"
        f"<form method=post action='/admin/announcements/add'>"
        f"<input type=hidden name=csrf value='{html.escape(csrf)}'>"
        f"<label>Kind{kind_select}</label>"
        f"<label>Title<input name=title maxlength=120 required></label>"
        f"<label>Body<input name=body maxlength=240></label>"
        f"<label>CTA label<input name=cta_label maxlength=40></label>"
        f"<label>Action type{action_select}</label>"
        f"<label>Action value (URL or story cluster id)<input name=action_value maxlength=500></label>"
        f"<label>Starts (UTC)<input type=datetime-local name=starts_at required></label>"
        f"<label>Ends (UTC)<input type=datetime-local name=ends_at required></label>"
        f"<label>Priority (higher shows first)<input type=number name=priority value=0></label>"
        f"<button>Add announcement</button></form>"
        f"<h2>Scheduled ({len(rows)})</h2>{table}"
    )
    return layout(TITLE, body, current="/admin/announcements")


@router.post("/add")
async def add_announcement(request: Request, db: AsyncSession = Depends(get_db)):
    fields = await form_fields(request)
    verify(request, fields)

    kind = fields.get("kind", "info")
    if kind not in KINDS:
        raise HTTPException(status_code=400, detail="invalid kind")
    title = fields.get("title", "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="title is required")
    body = (fields.get("body") or "").strip() or None
    cta_label = (fields.get("cta_label") or "").strip() or None
    action_type = fields.get("action_type") or ""
    if action_type not in ACTION_TYPES:
        raise HTTPException(status_code=400, detail="invalid action_type")
    action_value = (fields.get("action_value") or "").strip() or None
    starts_at = _parse_local_dt(fields.get("starts_at", ""), "starts_at")
    ends_at = _parse_local_dt(fields.get("ends_at", ""), "ends_at")
    if ends_at <= starts_at:
        raise HTTPException(status_code=400, detail="ends_at must be after starts_at")
    try:
        priority = int(fields.get("priority") or 0)
    except ValueError:
        raise HTTPException(status_code=400, detail="priority must be an integer")

    db.add(Announcement(
        kind=kind,
        title=title,
        body=body,
        cta_label=cta_label,
        action_type=action_type or None,
        action_value=action_value,
        starts_at=starts_at,
        ends_at=ends_at,
        priority=priority,
    ))
    await db.commit()
    await _invalidate_active_cache()
    return RedirectResponse("/admin/announcements", status_code=303)


@router.post("/{announcement_id}/delete")
async def delete_announcement(announcement_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    fields = await form_fields(request)
    verify(request, fields)
    await db.execute(delete(Announcement).where(Announcement.id == announcement_id))
    await db.commit()
    await _invalidate_active_cache()
    return RedirectResponse("/admin/announcements", status_code=303)
