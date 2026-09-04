"""Human review gate for the AI-drafted daily poll.

Sign-in is shared with the quiz review page (app/admin_session.py) so one
login and one notification cover both of the reviewer's daily tasks.
"""
import html
from datetime import datetime
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
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
    verify,
)
from app.database import get_db
from app.models import DailyPoll, PollOption
from app.services.polls import IST, approve_poll, generate_draft

router = APIRouter(prefix="/admin/polls")
TITLE = "Daily Poll Review"


@router.get("/login", response_class=HTMLResponse)
async def login_page():
    return login_form(TITLE, "/admin/polls/login")


@router.post("/login")
async def login(request: Request):
    fields = await form_fields(request)
    if not credentials_match(fields):
        return layout(TITLE, "<h1>Sign in failed</h1><p class=danger>Invalid credentials.</p><a href='/admin/polls/login'>Try again</a>")
    response = RedirectResponse("/admin/polls", status_code=303)
    set_session_cookie(response, request)
    return response


@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    csrf = session_csrf(request)
    if not csrf: return RedirectResponse("/admin/polls/login", status_code=303)
    poll = await db.scalar(select(DailyPoll).where(DailyPoll.poll_date == datetime.now(IST).date()))
    if not poll:
        return layout(TITLE, f"<h1>Daily Poll</h1><p>No draft exists for today.</p><form method=post action='/admin/polls/generate'><input type=hidden name=csrf value='{csrf}'><button>Generate draft</button></form>")
    options = (await db.execute(select(PollOption).where(PollOption.poll_id == poll.id).order_by(PollOption.position))).scalars().all()
    option_inputs = "".join(f"<label>Option {i+1}<input name=option value='{html.escape(option.text, quote=True)}'></label>" for i, option in enumerate(options))
    source_text = html.escape(poll.source_headline or "Evergreen fallback")
    source = f"<p><b>Source:</b> <a target=_blank href='/api/v1/clusters/{poll.source_cluster_id}'>{source_text}</a></p>" if poll.source_cluster_id else f"<p><b>Source:</b> {source_text}</p>"
    editable = poll.status == "draft" and datetime.now(IST) < poll.publish_at
    controls = "<button name=action value=approve>Approve for 9:00 AM</button><button name=action value=regenerate>Regenerate</button><button name=action value=reject>Reject and use fallback</button>" if editable else "<p>This poll can no longer be edited.</p>"
    return layout(TITLE, f"<h1>Daily Poll — {poll.poll_date}</h1><p class=meta>Status: {poll.status} · Publishes 9:00 AM IST</p>{source}<form method=post action='/admin/polls/update'><input type=hidden name=csrf value='{csrf}'><input type=hidden name=poll_id value='{poll.id}'><label>Question<textarea name=question required>{html.escape(poll.question)}</textarea></label><label>Context<textarea name=context required>{html.escape(poll.context)}</textarea></label>{option_inputs}{controls}</form>")


def _error_page(csrf: str, message: str) -> HTMLResponse:
    return layout(TITLE, f"<h1>Daily Poll</h1><p class=danger>Draft generation failed: {html.escape(message)}</p><form method=post action='/admin/polls/generate'><input type=hidden name=csrf value='{csrf}'><button>Try again</button></form>")


@router.post("/generate")
async def generate(request: Request, db: AsyncSession = Depends(get_db)):
    fields = await form_fields(request); verify(request, fields)
    try:
        await generate_draft(db, datetime.now(IST).date())
    except HTTPException:
        raise
    except Exception as exc:
        return _error_page(fields.get("csrf", ""), str(exc))
    return RedirectResponse("/admin/polls", status_code=303)


@router.post("/update")
async def update(request: Request, db: AsyncSession = Depends(get_db)):
    fields = await form_fields(request); verify(request, fields)
    poll_id = int(fields["poll_id"])
    action = fields.get("action")
    if action == "regenerate":
        try:
            await generate_draft(db, datetime.now(IST).date(), replace=True)
        except HTTPException:
            raise
        except Exception as exc:
            return _error_page(fields.get("csrf", ""), str(exc))
    elif action == "reject":
        poll = await db.get(DailyPoll, poll_id)
        if not poll or poll.status != "draft": raise HTTPException(status_code=409, detail="Draft cannot be rejected")
        poll.status = "rejected"; await db.commit()
    else:
        # parse_qs collapses repeated fields in _fields; re-read them here.
        raw = parse_qs((await request.body()).decode(), keep_blank_values=True)
        options = raw.get("option", [])
        await approve_poll(db, poll_id, fields.get("question", ""), fields.get("context", ""), options)
    return RedirectResponse("/admin/polls", status_code=303)
