"""The reviewer's landing page: what needs a decision today, in one place.

Exists because there is one notification for one person doing two jobs. Deep
linking the push straight at either review page would leave the other one
silently waiting, which is exactly the failure mode a single notification is
supposed to prevent.
"""
from __future__ import annotations

import html
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_session import (
    credentials_match,
    form_fields,
    layout,
    login_form,
    session_csrf,
    set_session_cookie,
)
from app.database import get_db
from app.models import DailyPoll, DailyQuiz
from app.services.polls import IST

router = APIRouter(prefix="/admin")
TITLE = "Daily Review"


async def pending_reviews(db: AsyncSession, day) -> dict[str, dict]:
    """What is waiting on the reviewer for `day`.

    Shared with the notifier (app/services/admin_notify.py) so the push and the
    page can never disagree about what is outstanding.
    """
    poll = await db.scalar(select(DailyPoll).where(DailyPoll.poll_date == day))
    quiz = await db.scalar(select(DailyQuiz).where(DailyQuiz.puzzle_date == day))
    return {
        "poll": {
            "exists": poll is not None,
            "status": poll.status if poll else None,
            "waiting": bool(poll and poll.status == "draft"),
            "summary": poll.question if poll else "No draft was generated",
            "url": "/admin/polls",
        },
        "quiz": {
            "exists": quiz is not None,
            "status": quiz.status if quiz else None,
            "waiting": bool(quiz and quiz.status == "draft"),
            "summary": (
                f"{len(quiz.questions)} questions ({quiz.source})" if quiz
                else "No draft was generated"),
            "url": "/admin/quiz",
        },
    }


def _task_card(name: str, task: dict) -> str:
    if task["waiting"]:
        state = "<b class=danger>Needs review</b>"
    elif not task["exists"]:
        state = "<b class=danger>Missing</b>"
    elif task["status"] == "rejected":
        state = "<span class=meta>Rejected — serving the fallback</span>"
    else:
        state = "<b class=done>Done</b>"
    return (
        f"<div class=task><h2>{name}</h2>"
        f"<p class=meta>{state} · status: {task['status'] or 'none'}</p>"
        f"<p>{html.escape(str(task['summary'])[:160])}</p>"
        f"<a href='{task['url']}'>Open {name.lower()} review →</a></div>")


@router.get("/login", response_class=HTMLResponse)
async def login_page():
    return login_form(TITLE, "/admin/login")


@router.post("/login")
async def login(request: Request):
    fields = await form_fields(request)
    if not credentials_match(fields):
        return layout(TITLE, "<h1>Sign in failed</h1><p class=danger>Invalid credentials.</p>"
                             "<a href='/admin/login'>Try again</a>")
    response = RedirectResponse("/admin", status_code=303)
    set_session_cookie(response, request)
    return response


@router.get("", response_class=HTMLResponse)
async def home(request: Request, db: AsyncSession = Depends(get_db)):
    if not session_csrf(request):
        return RedirectResponse("/admin/login", status_code=303)
    today = datetime.now(IST).date()
    tasks = await pending_reviews(db, today)
    waiting = sum(1 for task in tasks.values() if task["waiting"])
    heading = ("Nothing waiting on you" if not waiting
               else f"{waiting} item{'s' if waiting > 1 else ''} to review")
    return layout(TITLE, (
        f"<h1>Daily Review — {today}</h1><p class=meta>{heading}</p>"
        f"{_task_card('Poll', tasks['poll'])}"
        f"{_task_card('Quiz', tasks['quiz'])}"))
