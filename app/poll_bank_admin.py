"""Admin CRUD for the Poll question bank (app.models.PollFallback).

Separate page from poll_admin.py deliberately, same reason quiz_admin.py and
quiz_bank_admin.py stay separate: that page reviews one AI-drafted day; this
one manages the standing bank that activate_poll() publishes from when no
draft was approved by 9:00 AM (least-recently-used first). Sign-in is shared
via app.admin_session, same as every other admin page.
"""
from __future__ import annotations

import html
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_session import form_fields, layout, session_csrf, verify
from app.database import get_db
from app.models import PollFallback
from app.services.polls import draft_bank_poll, seed_fallbacks, validate_draft

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/poll-bank")
TITLE = "Poll Question Bank"
PAGE_SIZE = 25
MAX_OPTIONS = 4  # validate_draft accepts 2-4; the first two inputs are required
MAX_CATEGORY = 50  # PollFallback.category is VARCHAR(50)
AVOID_LIST_SIZE = 25  # existing questions shown to Claude so it doesn't repeat them


def _add_form(csrf: str, error: str | None = None, draft: dict | None = None) -> str:
    """`draft` pre-fills the form from a Claude-generated poll — still goes
    through this same form/submit so it's reviewed (and editable) before it's
    saved, never written to the bank directly."""
    draft = draft or {}
    error_html = f"<p class=danger>{html.escape(error)}</p>" if error else ""
    draft_options = draft.get("options") or []
    options = "".join(
        f"<label>Option {i + 1}{'' if i < 2 else ' (optional)'}"
        f"<input name=option_{i}{' required' if i < 2 else ''} "
        f"value='{html.escape(draft_options[i] if i < len(draft_options) else '')}'></label>"
        for i in range(MAX_OPTIONS)
    )
    return (
        "<section class=task><h2>Add a poll</h2>"
        f"{error_html}"
        "<form method=post action='/admin/poll-bank/generate' style='margin-bottom:1em'>"
        f"<input type=hidden name=csrf value='{csrf}'>"
        "<label>Category for Claude to generate (optional)"
        f"<input name=gen_category maxlength={MAX_CATEGORY} placeholder='e.g. education, environment' "
        f"value='{html.escape(draft.get('category') or '')}'></label>"
        "<button>Generate via Claude</button>"
        "</form>"
        "<form method=post action='/admin/poll-bank/add'>"
        f"<input type=hidden name=csrf value='{csrf}'>"
        f"<label>Question<textarea name=question required>{html.escape(draft.get('question', ''))}</textarea></label>"
        f"<label>Context<textarea name=context required>{html.escape(draft.get('context', ''))}</textarea></label>"
        f"{options}"
        f"<label>Category (optional)<input name=category maxlength={MAX_CATEGORY} placeholder='e.g. education, environment' "
        f"value='{html.escape(draft.get('category') or '')}'></label>"
        "<button>Add to bank</button>"
        "</form></section>")


def _tabs(active: str) -> str:
    """Render the two poll-bank views as navigation tabs.

    Same GET-form approach (and public, un-prefixed paths) as
    quiz_bank_admin._tabs — the admin subdomain proxy rewrites the public path
    before forwarding it to the internal /admin route.
    """
    tabs = (
        ("create", "Add poll", "/poll-bank?tab=create"),
        ("list", "All polls", "/poll-bank?tab=list"),
    )
    links = []
    for key, label, _href in tabs:
        current_class = " current" if key == active else ""
        current_attr = ' aria-current="page"' if key == active else ""
        links.append(
            f"<form method='get' action='/poll-bank' class='admin-tab-form'>"
            f"<input type='hidden' name='tab' value='{key}'>"
            f"<button type='submit' class='admin-tab{current_class}'"
            f"{current_attr}>{label}</button>"
            "</form>"
        )
    return (
        "<nav class='admin-tabs' aria-label='Poll question bank views'>"
        + "".join(links)
        + "</nav>"
    )


def _create_page(csrf: str, error: str | None = None, draft: dict | None = None) -> HTMLResponse:
    return layout(TITLE, (
        "<h1>Poll Question Bank</h1>"
        f"{_tabs('create')}"
        f"{_add_form(csrf, error, draft)}"
        "<p><a href='/poll-bank?tab=list'>See all polls</a></p>"), current="/admin/poll-bank")


@router.get("", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    page: int = 1,
    tab: str = Query("create", pattern="^(create|list)$"),
    db: AsyncSession = Depends(get_db),
):
    csrf = session_csrf(request)
    if not csrf:
        return RedirectResponse("/admin/quiz/login", status_code=303)
    page = max(page, 1)

    # Seed the built-in polls now rather than lazily on first activation, so
    # they show up here — and so adding a poll to a still-empty table can't
    # suppress the seed (seed_fallbacks skips a non-empty table).
    await seed_fallbacks(db)

    total = (await db.execute(select(func.count()).select_from(PollFallback))).scalar_one()
    active_total = (await db.execute(
        select(func.count()).where(PollFallback.active.is_(True))
    )).scalar_one()
    rows = []
    if tab == "list":
        rows = (await db.execute(
            select(PollFallback).order_by(PollFallback.created_at.desc(), PollFallback.id.desc())
            .offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)
        )).scalars().all()

    def _row(p: PollFallback) -> str:
        options = "<br>".join(html.escape(o) for o in p.options)
        toggle_label = "Deactivate" if p.active else "Activate"
        return (
            f"<tr><td>{html.escape(p.question)}"
            f"<br><span class=meta>{html.escape(p.context)}</span>"
            f"<br><span class=meta>{options}</span></td>"
            f"<td>{html.escape(p.category or '—')}</td>"
            f"<td>{'yes' if p.active else 'no'}</td>"
            f"<td>{p.used_count}</td>"
            f"<td>{p.created_at:%Y-%m-%d}</td>"
            f"<td>"
            f"<form method=post action='/admin/poll-bank/{p.id}/toggle' style='display:inline'>"
            f"<input type=hidden name=csrf value='{csrf}'><button>{toggle_label}</button></form> "
            f"<form method=post action='/admin/poll-bank/{p.id}/delete' style='display:inline' "
            f"onsubmit='return confirm(\"Delete this poll?\")'>"
            f"<input type=hidden name=csrf value='{csrf}'><button class=danger>Delete</button></form>"
            f"</td></tr>")

    table = (
        "<table style='width:100%;border-collapse:collapse'>"
        "<tr><th align=left>Poll</th><th align=left>Category</th>"
        "<th align=left>Active</th><th align=left>Used</th><th align=left>Added</th><th></th></tr>"
        + "".join(_row(p) for p in rows) + "</table>") if rows else "<p class=meta>No polls in the bank yet.</p>"

    pager = ""
    if tab == "list":
        if page > 1:
            pager += f"<a href='/poll-bank?tab=list&page={page - 1}'>← newer</a> "
        if total > page * PAGE_SIZE:
            pager += f"<a href='/poll-bank?tab=list&page={page + 1}'>older →</a>"

    empty_warning = (
        "<p class=danger>No active polls — if no draft is approved by 9:00 AM the "
        "daily poll will fail to publish. Activate or add one.</p>"
    ) if active_total == 0 else ""
    content = _add_form(csrf) if tab == "create" else table + f"<p>{pager}</p>"

    return layout(TITLE, (
        "<h1>Poll Question Bank</h1>"
        f"<p class=meta>{total} poll(s) · {active_total} active — the daily poll "
        "is drawn from here (least recently used first) when no AI draft is "
        "approved by 9:00 AM IST.</p>"
        f"{empty_warning}"
        f"{_tabs(tab)}"
        f"{content}"), current="/admin/poll-bank")


@router.post("/generate")
async def generate(request: Request, db: AsyncSession = Depends(get_db)):
    """Draft one poll with Claude and hand it back into the same add-poll form
    for review — never saved directly. Follows
    [[llm-generation-human-review-gate]]: generate greedily, let the human
    reviewer be the only gate before it's active in the bank."""
    fields = await form_fields(request)
    verify(request, fields)
    csrf = fields.get("csrf", "")

    category = (fields.get("gen_category") or "").strip()[:MAX_CATEGORY] or None
    existing = (await db.execute(
        select(PollFallback.question).order_by(PollFallback.id.desc()).limit(AVOID_LIST_SIZE)
    )).scalars().all()

    error = None
    draft: dict = {}
    try:
        result = await draft_bank_poll(category, list(existing))
    except ValueError as exc:
        logger.warning("Bank poll generation failed validation: %s", exc)
        error = f"Claude's draft failed validation ({exc}) — try again."
    else:
        if result is None:
            error = "Claude request failed — try again."
        else:
            draft = result

    return _create_page(csrf, error, draft)


@router.post("/add")
async def add(request: Request, db: AsyncSession = Depends(get_db)):
    fields = await form_fields(request)
    verify(request, fields)
    csrf = fields.get("csrf", "")

    # Blank optional inputs are dropped; validate_draft enforces the 2-4 count.
    options = [o for o in ((fields.get(f"option_{i}") or "").strip() for i in range(MAX_OPTIONS)) if o]
    category = (fields.get("category") or "").strip() or None
    draft = {
        "question": fields.get("question", ""), "context": fields.get("context", ""),
        "options": options, "category": category,
    }

    try:
        question, context, options = validate_draft(draft)
    except ValueError as exc:
        return _create_page(csrf, str(exc), draft)
    if category and len(category) > MAX_CATEGORY:
        return _create_page(csrf, f"Category must be {MAX_CATEGORY} characters or fewer.", draft)

    await seed_fallbacks(db)
    duplicate = await db.scalar(
        select(PollFallback.id).where(func.lower(PollFallback.question) == question.lower()).limit(1)
    )
    if duplicate is not None:
        return _create_page(csrf, "That question is already in the bank.", draft)

    db.add(PollFallback(question=question, context=context, options=options, category=category))
    await db.commit()
    return RedirectResponse("/admin/poll-bank?tab=list", status_code=303)


@router.post("/{poll_id}/toggle")
async def toggle(poll_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    fields = await form_fields(request)
    verify(request, fields)
    poll = await db.get(PollFallback, poll_id)
    if poll is None:
        raise HTTPException(status_code=404, detail="Poll not found")
    poll.active = not poll.active
    await db.commit()
    return RedirectResponse("/admin/poll-bank?tab=list", status_code=303)


@router.post("/{poll_id}/delete")
async def delete(poll_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    fields = await form_fields(request)
    verify(request, fields)
    poll = await db.get(PollFallback, poll_id)
    if poll is None:
        raise HTTPException(status_code=404, detail="Poll not found")
    await db.delete(poll)
    await db.commit()
    return RedirectResponse("/admin/poll-bank?tab=list", status_code=303)
