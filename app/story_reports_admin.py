"""Reviewing user-submitted story reports (misleading/offensive/etc flags).

Used to carry its own copy of the signed-cookie session under a `poll_admin`
cookie (see git history) — same credentials as everything else, but a
separate sign-in, so opening this page after signing in anywhere else in the
admin still prompted for a password. Migrated onto the shared
app.admin_session cookie 2026-09-08 to close that gap; no other page carries
its own auth plumbing anymore.
"""
from __future__ import annotations

import html

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
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
from app.models import StoryReport

router = APIRouter(prefix="/admin/reports")
TITLE = "Story Reports"

REASON_LABELS = {
    "misleading": "Misleading / Clickbait",
    "factually_incorrect": "Factually incorrect",
    "offensive": "Offensive / Inappropriate",
    "duplicate_spam": "Duplicate / Spam",
    "other": "Other",
}


@router.get("/login", response_class=HTMLResponse)
async def login_page():
    return login_form(TITLE, "/admin/reports/login")


@router.post("/login")
async def login(request: Request):
    fields = await form_fields(request)
    if not credentials_match(fields):
        return layout(TITLE, "<h1>Sign in failed</h1><p class=danger>Invalid credentials.</p>"
                             "<a href='/admin/reports/login'>Try again</a>")
    response = RedirectResponse("/admin/reports", status_code=303)
    set_session_cookie(response, request)
    return response


@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    csrf = session_csrf(request)
    if not csrf:
        return RedirectResponse("/admin/reports/login", status_code=303)

    reports = (await db.execute(
        select(StoryReport).where(StoryReport.status == "open").order_by(StoryReport.created_at.desc())
    )).scalars().all()

    if not reports:
        return layout(TITLE, f"<h1>Story Reports</h1>{nav('/admin/reports')}<p>No open reports.</p>")

    rows = []
    for report in reports:
        reason_text = html.escape(REASON_LABELS.get(report.reason, report.reason))
        note_text = f"<p>{html.escape(report.note)}</p>" if report.note else ""
        source = (
            f"<a target=_blank href='/api/v1/clusters/{report.cluster_id}'>cluster {report.cluster_id}</a>"
            if report.cluster_id else "cluster deleted"
        )
        rows.append(
            f"<div class=report><p class=meta>{report.created_at} · reported by {html.escape(report.user_id)} · {source}</p>"
            f"<p><b>{reason_text}</b></p>{note_text}"
            f"<form method=post action='/admin/reports/update'><input type=hidden name=csrf value='{csrf}'>"
            f"<input type=hidden name=report_id value='{report.id}'>"
            f"<button name=action value=reviewed>Mark reviewed</button>"
            f"<button name=action value=dismissed>Dismiss</button></form></div>"
        )
    return layout(TITLE, f"<h1>Story Reports</h1>{nav('/admin/reports')}{''.join(rows)}")


@router.post("/update")
async def update(request: Request, db: AsyncSession = Depends(get_db)):
    fields = await form_fields(request)
    verify(request, fields)
    report = await db.get(StoryReport, int(fields["report_id"]))
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    action = fields.get("action")
    if action not in ("reviewed", "dismissed"):
        raise HTTPException(status_code=400, detail="Invalid action")
    report.status = action
    await db.commit()
    return RedirectResponse("/admin/reports", status_code=303)
