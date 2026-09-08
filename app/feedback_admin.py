"""Reading and triaging what people send through the website's /feedback form.

Reuses app.admin_session rather than minting its own cookie, so this shares the
one sign-in with /admin and /admin/quiz. (app/story_reports_admin.py predates
that module and still carries its own copy of the same plumbing under the older
`poll_admin` cookie — it is the odd one out, not the pattern to follow.)

The list is paged and filtered by status rather than showing everything: unlike
the poll and quiz reviews, which are empty most of the time by construction,
this table only grows, and "everything ever sent" stops being a useful screen
about a week in.
"""
from __future__ import annotations

import html

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
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
from app.models import Feedback

router = APIRouter(prefix="/admin/feedback")
TITLE = "Feedback"

PAGE_SIZE = 25

CATEGORY_LABELS = {
    "bug": "Something is broken",
    "story": "A story is wrong or misleading",
    "feature": "An idea or a request",
    "publisher": "Publisher — include or remove",
    "other": "Something else",
}

# The order they appear as tabs, and the only values /update will write.
STATUSES = ("new", "read", "closed")


@router.get("/login", response_class=HTMLResponse)
async def login_page():
    return login_form(TITLE, "/admin/feedback/login")


@router.post("/login")
async def login(request: Request):
    fields = await form_fields(request)
    if not credentials_match(fields):
        return layout(TITLE, "<h1>Sign in failed</h1><p class=danger>Invalid credentials.</p>"
                             "<a href='/admin/feedback/login'>Try again</a>")
    response = RedirectResponse("/admin/feedback", status_code=303)
    set_session_cookie(response, request)
    return response


def _entry(item: Feedback, csrf: str) -> str:
    category = html.escape(CATEGORY_LABELS.get(item.category, item.category))

    # Someone who left neither a name nor an email sent this anonymously and
    # that is a supported way to use the form, so say so plainly rather than
    # rendering two empty fields.
    if item.email:
        who = f"<a href='mailto:{html.escape(item.email)}'>{html.escape(item.name or item.email)}</a>"
        if item.name:
            who += f" &lt;{html.escape(item.email)}&gt;"
        who += " · <b>wants a reply</b>"
    elif item.name:
        who = f"{html.escape(item.name)} · no email, cannot reply"
    else:
        who = "anonymous"

    # white-space:pre-wrap: people write in paragraphs, and collapsing them
    # turns a readable report into a wall.
    body = f"<p style='white-space:pre-wrap'>{html.escape(item.message)}</p>"

    buttons = "".join(
        f"<button name=action value={status}>{'Reopen' if status == 'new' else status.capitalize()}</button>"
        for status in STATUSES if status != item.status
    )

    return (
        f"<div class=task><h2>{category}</h2>"
        f"<p class=meta>#{item.id} · {item.created_at:%Y-%m-%d %H:%M} UTC · {item.source} · {who}</p>"
        f"{body}"
        f"<form method=post action='/admin/feedback/update'>"
        f"<input type=hidden name=csrf value='{html.escape(csrf)}'>"
        f"<input type=hidden name=feedback_id value='{item.id}'>"
        f"<input type=hidden name=back value='{html.escape(item.status)}'>"
        f"{buttons}</form></div>")


@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request, status: str = "new", page: int = 1,
                    db: AsyncSession = Depends(get_db)):
    csrf = session_csrf(request)
    if not csrf:
        return RedirectResponse("/admin/feedback/login", status_code=303)
    if status not in STATUSES:
        status = "new"
    page = max(page, 1)

    counts = dict((await db.execute(
        select(Feedback.status, func.count()).group_by(Feedback.status)
    )).all())

    items = (await db.execute(
        select(Feedback)
        .where(Feedback.status == status)
        .order_by(Feedback.created_at.desc())
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
    )).scalars().all()

    tabs = " · ".join(
        f"<b>{name} ({counts.get(name, 0)})</b>" if name == status
        else f"<a href='/admin/feedback?status={name}'>{name} ({counts.get(name, 0)})</a>"
        for name in STATUSES)

    if items:
        entries = "".join(_entry(item, csrf) for item in items)
    else:
        entries = f"<p class=meta>Nothing {html.escape(status)}.</p>"

    total = counts.get(status, 0)
    pager = ""
    if page > 1:
        pager += f"<a href='/admin/feedback?status={status}&page={page - 1}'>← newer</a> "
    if total > page * PAGE_SIZE:
        pager += f"<a href='/admin/feedback?status={status}&page={page + 1}'>older →</a>"

    return layout(TITLE, (
        f"<h1>Feedback</h1>{nav('/admin/feedback')}<p class=meta>{tabs}</p>"
        f"{entries}<p>{pager}</p>"))


@router.post("/update")
async def update(request: Request, db: AsyncSession = Depends(get_db)):
    fields = await form_fields(request)
    verify(request, fields)

    action = fields.get("action")
    if action not in STATUSES:
        raise HTTPException(status_code=400, detail="Invalid action")

    item = await db.get(Feedback, int(fields["feedback_id"]))
    if not item:
        raise HTTPException(status_code=404, detail="Feedback not found")
    item.status = action
    await db.commit()

    # Back to the tab they were looking at, not to the one the item moved to:
    # working through "new" should not jump the reviewer somewhere else on
    # every click.
    back = fields.get("back")
    if back not in STATUSES:
        back = "new"
    return RedirectResponse(f"/admin/feedback?status={back}", status_code=303)
