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

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_session import form_fields, layout, nav, session_csrf, verify
from app.database import get_db
from app.models import QuizBankQuestion, utc_now

router = APIRouter(prefix="/admin/quiz-bank")
TITLE = "Quiz Question Bank"
PAGE_SIZE = 25
OPTION_COUNT = 4


def _add_form(csrf: str, error: str | None = None) -> str:
    error_html = f"<p class=danger>{html.escape(error)}</p>" if error else ""
    options = "".join(
        f"<label>Option {i + 1}<input name=option_{i} required></label>"
        for i in range(OPTION_COUNT)
    )
    correct_choices = "".join(
        f"<option value={i}>Option {i + 1}</option>" for i in range(OPTION_COUNT)
    )
    return (
        "<details open><summary><b>Add a question</b></summary>"
        f"{error_html}"
        f"<form method=post action='/admin/quiz-bank/add'>"
        f"<input type=hidden name=csrf value='{csrf}'>"
        "<label>Question<textarea name=question required></textarea></label>"
        f"{options}"
        f"<label>Correct answer<select name=correct_index>{correct_choices}</select></label>"
        "<label>Explanation<input name=explanation></label>"
        "<label>Category (optional)<input name=category placeholder='e.g. history, geography'></label>"
        "<button>Add to bank</button>"
        "</form></details>")


@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request, page: int = 1, db: AsyncSession = Depends(get_db)):
    csrf = session_csrf(request)
    if not csrf:
        return RedirectResponse("/admin/quiz/login", status_code=303)
    page = max(page, 1)

    total = (await db.execute(select(func.count()).select_from(QuizBankQuestion))).scalar_one()
    active_total = (await db.execute(
        select(func.count()).where(QuizBankQuestion.is_active.is_(True))
    )).scalar_one()
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
    if page > 1:
        pager += f"<a href='/admin/quiz-bank?page={page - 1}'>← newer</a> "
    if total > page * PAGE_SIZE:
        pager += f"<a href='/admin/quiz-bank?page={page + 1}'>older →</a>"

    return layout(TITLE, (
        f"<h1>Quiz Question Bank</h1>{nav('/admin/quiz-bank')}"
        f"<p class=meta>{total} question(s) · {active_total} active — "
        "generate_quiz() draws 5 active questions from here when Claude's draft "
        "fails validation, before falling back to the hardcoded curated set.</p>"
        f"{_add_form(csrf)}"
        f"{table}<p>{pager}</p>"))


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
            f"<h1>Quiz Question Bank</h1>{nav('/admin/quiz-bank')}"
            f"{_add_form(fields.get('csrf', ''), error)}"
            f"<p><a href='/admin/quiz-bank'>Back to the bank</a></p>"))

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
    return RedirectResponse("/admin/quiz-bank", status_code=303)


@router.post("/{question_id}/delete")
async def delete(question_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    fields = await form_fields(request)
    verify(request, fields)
    question = await db.get(QuizBankQuestion, question_id)
    if question is None:
        raise HTTPException(status_code=404, detail="Question not found")
    await db.delete(question)
    await db.commit()
    return RedirectResponse("/admin/quiz-bank", status_code=303)


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
