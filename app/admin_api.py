"""Core JSON API for the admin SPA (app/static/admin/): sign-in, the session
probe the SPA boots from, the overview behind the Today page and the sidebar
badges, and the navbar's global search.

Each section (polls, quiz, feedback, ...) has its own /admin/api/<section>
router in its own module, the same file that used to render that section's
HTML page. This module only holds what is shared across all of them.

Auth is the one signed cookie from app.admin_session. Reads depend on
require_admin; anything that writes depends on require_admin_write, which also
wants the session's CSRF token back in the X-CSRF-Token header.
"""
from __future__ import annotations

import importlib
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_home import pending_reviews
from app.admin_session import (
    COOKIE_NAME,
    credentials_match,
    require_admin,
    session_csrf,
    set_session_cookie,
)
from app.config import settings
from app.database import get_db
from app.models import Explainer, Feedback, ReadEvent, StoryCluster, StoryReport, User, utc_now
from app.services.polls import IST

router = APIRouter(prefix="/admin/api")


class LoginIn(BaseModel):
    username: str = ""
    password: str = ""


@router.post("/login")
async def login(body: LoginIn, request: Request):
    if not credentials_match({"username": body.username, "password": body.password}):
        raise HTTPException(status_code=401, detail="That username and password don't match.")
    response = JSONResponse({"ok": True})
    set_session_cookie(response, request)
    return response


@router.post("/logout")
async def logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE_NAME)
    return response


@router.get("/session")
async def session(request: Request):
    """Never 401s: the SPA calls this first to decide between the login
    screen and the app, and the CSRF token it returns is what every write
    sends back as X-CSRF-Token."""
    csrf = session_csrf(request)
    return {"signedIn": bool(csrf), "csrf": csrf, "username": settings.POLL_ADMIN_USERNAME if csrf else None}


# Per-section "needs attention" counts for the sidebar badges. Each entry
# names a module-level `async def pending_count(db) -> int` in that
# section's module; a section that doesn't define one simply gets no badge.
# Imported lazily so one section failing to import can't take the whole
# overview down with it.
BADGE_SOURCES = {
    "breaking": "app.admin_breaking",
    "explainers": "app.admin_explainers",
    "timelines": "app.admin_timelines",
}


async def _badge(module_name: str, db: AsyncSession) -> int:
    try:
        fn = getattr(importlib.import_module(module_name), "pending_count", None)
        return int(await fn(db)) if fn else 0
    except Exception:
        await db.rollback()
        return 0


@router.get("/overview", dependencies=[Depends(require_admin)])
async def overview(db: AsyncSession = Depends(get_db)):
    now_ist = datetime.now(IST)
    tasks = await pending_reviews(db, now_ist.date())
    feedback_new = await db.scalar(
        select(func.count()).select_from(Feedback).where(Feedback.status == "new")) or 0
    reports_open = await db.scalar(
        select(func.count()).select_from(StoryReport).where(StoryReport.status == "open")) or 0
    latest_report = await db.scalar(
        select(func.max(StoryReport.created_at)).where(StoryReport.status == "open"))

    badges = {
        "polls": int(tasks["poll"]["waiting"]),
        "quiz": int(tasks["quiz"]["waiting"]),
        "feedback": int(feedback_new),
        "reports": int(reports_open),
    }
    for section, module_name in BADGE_SOURCES.items():
        badges[section] = await _badge(module_name, db)

    return {
        "today": now_ist.date().isoformat(),
        "now": now_ist.isoformat(),
        "tasks": tasks,
        "badges": badges,
        "latestReportAt": latest_report.isoformat() if latest_report else None,
    }


@router.get("/overview/activity", dependencies=[Depends(require_admin)])
async def activity(db: AsyncSession = Depends(get_db)):
    """Signed-in readers per day for the Today chart. ReadEvent only exists for
    signed-in readers, so this undercounts total readership; the page says so.
    UTC days, because grouping by an IST date needs dialect-specific SQL and
    this has to run on SQLite in tests too."""
    since = utc_now() - timedelta(days=14)
    day = func.date(ReadEvent.opened_at)
    rows = (await db.execute(
        select(day, func.count(func.distinct(ReadEvent.user_id)), func.count())
        .where(ReadEvent.opened_at >= since).group_by(day)
    )).all()
    by_day = {str(d)[:10]: (readers, opens) for d, readers, opens in rows}
    days = [(since + timedelta(days=i + 1)).date().isoformat() for i in range(14)]
    return {"days": [{"day": d, "readers": by_day.get(d, (0, 0))[0], "opens": by_day.get(d, (0, 0))[1]}
                     for d in days]}


@router.get("/search", dependencies=[Depends(require_admin)])
async def search(q: str = "", db: AsyncSession = Depends(get_db)):
    """The navbar search. Pages are matched client-side; this covers the
    records worth jumping straight to. Stories are limited to the last 14
    days so an ILIKE over headlines stays a scan of recent rows, not the
    whole table."""
    q = q.strip()
    if len(q) < 2:
        return {"results": []}
    like = f"%{q}%"
    results = []

    users = (await db.execute(
        select(User).where(or_(User.email.ilike(like), User.display_name.ilike(like), User.id == q))
        .order_by(User.created_at.desc()).limit(5)
    )).scalars().all()
    results += [{"kind": "Users", "title": u.display_name or u.email, "sub": u.email,
                 "route": f"users?q={u.id}", "nav": "users"} for u in users]

    stories = (await db.execute(
        select(StoryCluster.id, StoryCluster.headline, StoryCluster.distinct_source_count)
        .where(StoryCluster.headline.ilike(like),
               StoryCluster.last_updated_at >= utc_now() - timedelta(days=14))
        .order_by(StoryCluster.last_updated_at.desc()).limit(6)
    )).all()
    results += [{"kind": "Stories", "title": headline, "sub": f"Story {cid} · {sources or 1} sources",
                 "url": f"/api/v1/clusters/{cid}", "nav": "breaking"} for cid, headline, sources in stories]

    explainers = (await db.execute(
        select(Explainer.id, Explainer.question, Explainer.status)
        .where(Explainer.question.ilike(like)).order_by(Explainer.id.desc()).limit(5)
    )).all()
    results += [{"kind": "Explainers", "title": question, "sub": status.replace("_", " "),
                 "route": f"explainers/{eid}/review", "nav": "explainers"} for eid, question, status in explainers]

    feedback = (await db.execute(
        select(Feedback.id, Feedback.message, Feedback.status)
        .where(or_(Feedback.message.ilike(like), Feedback.email.ilike(like)))
        .order_by(Feedback.created_at.desc()).limit(5)
    )).all()
    results += [{"kind": "Feedback", "title": (message or "")[:90], "sub": f"#{fid} · {status}",
                 "route": f"feedback?status={status}&open={fid}", "nav": "feedback"}
                for fid, message, status in feedback]

    return {"results": results}
