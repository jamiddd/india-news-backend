"""Admin control for manually-pushed "hot topic" tabs — see app/models.py's
AdminTopic and the public GET /topics/active endpoint in app/main.py. A
guaranteed-visibility lever for a story the algorithmic feed hasn't caught
up to yet: whatever word an admin adds here becomes its own tab to the
left of "For You" in the app, for that calendar date only.

Same session/CSRF/nav/login plumbing as app/admin_breaking.py.
"""
from __future__ import annotations

import html
from datetime import date

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends
from fastapi.responses import HTMLResponse, RedirectResponse

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
from app.models import AdminTopic
from app.redis_client import get_redis_client
from app.services.crossword import india_today


async def _invalidate_active_cache() -> None:
    # GET /topics/active caches under this key (see app/main.py) for
    # CACHE_TTL_SECONDS — without this, an admin's add/delete wouldn't be
    # visible in the app for up to 5 minutes. Same fail-open-on-error
    # convention as _cache_get/_cache_set: caching is a perf optimization,
    # never a correctness dependency.
    try:
        await get_redis_client().delete("topics:active")
    except Exception:
        pass

router = APIRouter(prefix="/admin/topics")
TITLE = "Topics"


@router.get("/login", response_class=HTMLResponse)
async def login_page():
    return login_form(TITLE, "/admin/topics/login")


@router.post("/login")
async def login(request: Request):
    fields = await form_fields(request)
    if not credentials_match(fields):
        return layout(TITLE, "<h1>Sign in failed</h1><p class=danger>Invalid credentials.</p>"
                             "<a href='/admin/topics/login'>Try again</a>")
    response = RedirectResponse("/admin/topics", status_code=303)
    set_session_cookie(response, request)
    return response


def _row(row: AdminTopic, csrf: str) -> str:
    return (
        f"<tr><td>{row.topic_date.isoformat()}</td>"
        f"<td>{html.escape(row.word)}</td>"
        f"<td>{row.display_order}</td>"
        f"<td><form method=post action='/admin/topics/{row.id}/delete'>"
        f"<input type=hidden name=csrf value='{html.escape(csrf)}'>"
        f"<button class=danger>Delete</button></form></td></tr>"
    )


@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    csrf = session_csrf(request)
    if not csrf:
        return RedirectResponse("/admin/topics/login", status_code=303)

    today = india_today()
    rows = (await db.execute(
        select(AdminTopic).where(AdminTopic.topic_date >= today)
        .order_by(AdminTopic.topic_date, AdminTopic.display_order, AdminTopic.id)
    )).scalars().all()

    table = (
        "<table><tr><th>Date</th><th>Word</th><th>Order</th><th></th></tr>"
        + "".join(_row(r, csrf) for r in rows)
        + "</table>"
        if rows else "<p class=meta>No topics scheduled from today onward.</p>"
    )

    body = (
        f"<h1>Topics</h1>{nav('/admin/topics')}"
        f"<p class=meta>Each row becomes its own tab to the left of \"For You\" in the app, "
        f"for that date only (India calendar) — auto-expires the next day, nothing to clean up. "
        f"Today is {today.isoformat()}.</p>"
        f"<form method=post action='/admin/topics/add'>"
        f"<input type=hidden name=csrf value='{html.escape(csrf)}'>"
        f"<label>Date<input type=date name=topic_date value='{today.isoformat()}' required></label>"
        f"<label>Word<input name=word maxlength=60 required placeholder='e.g. Elections'></label>"
        f"<label>Order (lower shows first)<input type=number name=display_order value=0></label>"
        f"<button>Add topic</button></form>"
        f"<h2>Scheduled ({len(rows)})</h2>{table}"
    )
    return layout(TITLE, body)


@router.post("/add")
async def add_topic(request: Request, db: AsyncSession = Depends(get_db)):
    fields = await form_fields(request)
    verify(request, fields)

    word = fields.get("word", "").strip()
    if not word:
        raise HTTPException(status_code=400, detail="word is required")
    try:
        topic_date = date.fromisoformat(fields.get("topic_date", ""))
    except ValueError:
        raise HTTPException(status_code=400, detail="topic_date must be YYYY-MM-DD")
    try:
        display_order = int(fields.get("display_order") or 0)
    except ValueError:
        raise HTTPException(status_code=400, detail="display_order must be an integer")

    db.add(AdminTopic(topic_date=topic_date, word=word, display_order=display_order))
    await db.commit()
    await _invalidate_active_cache()
    return RedirectResponse("/admin/topics", status_code=303)


@router.post("/{topic_id}/delete")
async def delete_topic(topic_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    fields = await form_fields(request)
    verify(request, fields)
    await db.execute(delete(AdminTopic).where(AdminTopic.id == topic_id))
    await db.commit()
    await _invalidate_active_cache()
    return RedirectResponse("/admin/topics", status_code=303)
