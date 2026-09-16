"""Admin CRUD for the Quiz question bank (app.models.QuizBankQuestion).

Separate page from quiz_admin.py deliberately, same reason poll/quiz stay
separate: that page reviews one AI-drafted day; this one manages a standing
bank of hand-entered questions that generate_quiz() falls back to when
Claude's draft fails validation (see services/daily_games.py:bank_quiz_
questions). Sign-in is shared via app.admin_session, same as every other
admin page.
"""
from __future__ import annotations

import html
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_session import form_fields, layout, session_csrf, verify
from app.database import get_db
from app.models import QuizBankQuestion, utc_now
from app.services.daily_games import BANK_QUIZ_SYSTEM, validate_quiz_question
from app.services.llm_gen import call_claude_json

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/quiz-bank")
TITLE = "Quiz Question Bank"
PAGE_SIZE = 25
OPTION_COUNT = 4


def _add_form(csrf: str, error: str | None = None, draft: dict | None = None) -> str:
    """`draft` pre-fills the form from a Claude-generated question — still
    goes through this same form/submit so it's reviewed (and editable)
    before it's saved, never written to the bank directly."""
    draft = draft or {}
    error_html = f"<p class=danger>{html.escape(error)}</p>" if error else ""
    draft_options = draft.get("options") or [""] * OPTION_COUNT
    correct_index = draft.get("correct_index")
    options = "".join(
        f"<label>Option {i + 1}<input name=option_{i} required "
        f"value='{html.escape(draft_options[i] if i < len(draft_options) else '')}'></label>"
        for i in range(OPTION_COUNT)
    )
    correct_choices = "".join(
        f"<option value={i}{' selected' if correct_index == i else ''}>Option {i + 1}</option>"
        for i in range(OPTION_COUNT)
    )
    return (
        "<section class=task><h2>Add a question</h2>"
        f"{error_html}"
        "<form method=post action='/admin/quiz-bank/generate' style='margin-bottom:1em'>"
        f"<input type=hidden name=csrf value='{csrf}'>"
        "<label>Category for Claude to generate (optional)"
        f"<input name=gen_category placeholder='e.g. history, geography' "
        f"value='{html.escape(draft.get('category') or '')}'></label>"
        "<button>Generate via Claude</button>"
        "</form>"
        f"<form method=post action='/admin/quiz-bank/add'>"
        f"<input type=hidden name=csrf value='{csrf}'>"
        f"<label>Question<textarea name=question required>{html.escape(draft.get('question', ''))}</textarea></label>"
        f"{options}"
        f"<label>Correct answer<select name=correct_index>{correct_choices}</select></label>"
        f"<label>Explanation<input name=explanation value='{html.escape(draft.get('explanation') or '')}'></label>"
        f"<label>Category (optional)<input name=category placeholder='e.g. history, geography' "
        f"value='{html.escape(draft.get('category') or '')}'></label>"
        "<button>Add to bank</button>"
        "</form></section>")


def _tabs(active: str) -> str:
    """Render the two question-bank views as navigation tabs.

    Use GET forms rather than text links because the admin subdomain proxy
    rewrites the public path before forwarding it to the internal /admin
    route. The submit buttons make the interaction unambiguous in browsers.
    """
    tabs = (
        # These are public paths on admin.openindiannews.com. Keeping them
        # public here avoids relying on layout()'s internal /admin URL rewrite
        # for query-string navigation.
        ("create", "Add question", "/quiz-bank?tab=create"),
        ("list", "All questions", "/quiz-bank?tab=list"),
    )
    links = []
    for key, label, href in tabs:
        current_class = " current" if key == active else ""
        current_attr = ' aria-current="page"' if key == active else ""
        links.append(
            f"<form method='get' action='/quiz-bank' class='admin-tab-form'>"
            f"<input type='hidden' name='tab' value='{key}'>"
            f"<button type='submit' class='admin-tab{current_class}'"
            f"{current_attr}>{label}</button>"
            "</form>"
        )
    return (
        "<nav class='admin-tabs' aria-label='Quiz question bank views'>"
        + "".join(links)
        + "</nav>"
    )


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

    total = (await db.execute(select(func.count()).select_from(QuizBankQuestion))).scalar_one()
    active_total = (await db.execute(
        select(func.count()).where(QuizBankQuestion.is_active.is_(True))
    )).scalar_one()
    rows = []
    if tab == "list":
        rows = (await db.execute(
            select(QuizBankQuestion).order_by(QuizBankQuestion.created_at.desc())
            .offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)
        )).scalars().all()

    def _row(q: QuizBankQuestion) -> str:
        options = "<br>".join(
            f"{'★ ' if i == q.correct_index else ''}{html.escape(o)}"
            for i, o in enumerate(q.options))
        toggle_label = "Deactivate" if q.is_active else "Activate"
        return (
            f"<tr><td>{html.escape(q.question)}"
            f"<br><span class=meta>{options}</span></td>"
            f"<td>{html.escape(q.category or '—')}</td>"
            f"<td>{'yes' if q.is_active else 'no'}</td>"
            f"<td>{q.used_count}</td>"
            f"<td>{q.created_at:%Y-%m-%d}</td>"
            f"<td>"
            f"<form method=post action='/admin/quiz-bank/{q.id}/toggle' style='display:inline'>"
            f"<input type=hidden name=csrf value='{csrf}'><button>{toggle_label}</button></form> "
            f"<form method=post action='/admin/quiz-bank/{q.id}/delete' style='display:inline' "
            f"onsubmit='return confirm(\"Delete this question?\")'>"
            f"<input type=hidden name=csrf value='{csrf}'><button class=danger>Delete</button></form>"
            f"</td></tr>")

    table = (
        "<table style='width:100%;border-collapse:collapse'>"
        "<tr><th align=left>Question</th><th align=left>Category</th>"
        "<th align=left>Active</th><th align=left>Used</th><th align=left>Added</th><th></th></tr>"
        + "".join(_row(q) for q in rows) + "</table>") if rows else "<p class=meta>No questions in the bank yet.</p>"

    pager = ""
    if tab == "list":
        if page > 1:
            pager += f"<a href='/quiz-bank?tab=list&page={page - 1}'>← newer</a> "
        if total > page * PAGE_SIZE:
            pager += f"<a href='/quiz-bank?tab=list&page={page + 1}'>older →</a>"

    content = _add_form(csrf) if tab == "create" else table + f"<p>{pager}</p>"

    return layout(TITLE, (
        f"<h1>Quiz Question Bank</h1>"
        f"<p class=meta>{total} question(s) · {active_total} active — "
        "generate_quiz() draws 5 active questions from here when Claude's draft "
        "fails validation, before falling back to the hardcoded curated set.</p>"
        f"{_tabs(tab)}"
        f"{content}"), current="/admin/quiz-bank")


@router.post("/generate")
async def generate(request: Request, db: AsyncSession = Depends(get_db)):
    """Draft one question with Claude and hand it back into the same
    add-question form for review — never saved directly. Follows
    [[llm-generation-human-review-gate]]: generate greedily, let the
    human reviewer be the only gate before it's active in the bank."""
    fields = await form_fields(request)
    verify(request, fields)
    csrf = fields.get("csrf", "")

    category = (fields.get("gen_category") or "").strip()
    user_content = (
        f"Write a question about: {category}." if category
        else "Write a question on any general-knowledge topic."
    )

    error = None
    draft: dict = {}
    payload = await call_claude_json(BANK_QUIZ_SYSTEM, user_content, max_tokens=500)
    if payload is None:
        error = "Claude request failed — try again."
    else:
        try:
            draft = validate_quiz_question(payload)
            draft["category"] = category or None
        except Exception as exc:
            logger.warning("Bank quiz generation failed validation: %s", exc)
            error = f"Claude's draft failed validation ({exc}) — try again."

    return layout(TITLE, (
        f"<h1>Quiz Question Bank</h1>"
        f"{_tabs('create')}"
        f"{_add_form(csrf, error, draft)}"
        f"<p><a href='/quiz-bank?tab=list'>See all questions</a></p>"), current="/admin/quiz-bank")


@router.post("/add")
async def add(request: Request, db: AsyncSession = Depends(get_db)):
    fields = await form_fields(request)
    verify(request, fields)

    question = (fields.get("question") or "").strip()
    options = [(fields.get(f"option_{i}") or "").strip() for i in range(OPTION_COUNT)]
    explanation = (fields.get("explanation") or "").strip()
    category = (fields.get("category") or "").strip() or None
    try:
        correct_index = int(fields.get("correct_index", "0"))
    except ValueError:
        correct_index = -1

    error = None
    if not question:
        error = "Question can't be empty."
    elif any(not option for option in options):
        error = f"Need all {OPTION_COUNT} options filled in."
    elif len({option.casefold() for option in options}) != OPTION_COUNT:
        error = "Options must be distinct."
    elif not 0 <= correct_index < OPTION_COUNT:
        error = "Pick a valid correct answer."

    if error:
        return layout(TITLE, (
            f"<h1>Quiz Question Bank</h1>"
            f"{_tabs('create')}"
            f"{_add_form(fields.get('csrf', ''), error)}"
            f"<p><a href='/quiz-bank?tab=list'>See all questions</a></p>"), current="/admin/quiz-bank")

    db.add(QuizBankQuestion(
        question=question, options=options, correct_index=correct_index,
        explanation=explanation, category=category,
    ))
    await db.commit()
    return RedirectResponse("/admin/quiz-bank", status_code=303)


@router.post("/{question_id}/toggle")
async def toggle(question_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    fields = await form_fields(request)
    verify(request, fields)
    question = await db.get(QuizBankQuestion, question_id)
    if question is None:
        raise HTTPException(status_code=404, detail="Question not found")
    question.is_active = not question.is_active
    await db.commit()
    return RedirectResponse("/admin/quiz-bank?tab=list", status_code=303)


@router.post("/{question_id}/delete")
async def delete(question_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    fields = await form_fields(request)
    verify(request, fields)
    question = await db.get(QuizBankQuestion, question_id)
    if question is None:
        raise HTTPException(status_code=404, detail="Question not found")
    await db.delete(question)
    await db.commit()
    return RedirectResponse("/admin/quiz-bank?tab=list", status_code=303)


@router.get("/api/list")
async def list_api(
    request: Request,
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    active_only: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """Paginated JSON listing of the bank, session-gated like the rest of
    /admin — for tooling/scripts, not the mobile app (readers never see the
    bank directly; it only feeds generate_quiz()'s fallback tier)."""
    if not session_csrf(request):
        raise HTTPException(status_code=401, detail="Not signed in")

    base = select(QuizBankQuestion)
    if active_only:
        base = base.where(QuizBankQuestion.is_active.is_(True))

    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (await db.execute(
        base.order_by(QuizBankQuestion.created_at.desc()).offset(offset).limit(limit)
    )).scalars().all()

    return JSONResponse({
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(rows) < total,
        "items": [
            {
                "id": q.id,
                "question": q.question,
                "options": q.options,
                "correct_index": q.correct_index,
                "explanation": q.explanation,
                "category": q.category,
                "is_active": q.is_active,
                "used_count": q.used_count,
                "created_at": q.created_at.isoformat(),
                "last_used_at": q.last_used_at.isoformat() if q.last_used_at else None,
            }
            for q in rows
        ],
    })
