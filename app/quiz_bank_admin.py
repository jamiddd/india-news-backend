"""Admin CRUD for the Quiz question bank (app.models.QuizBankQuestion).

JSON API for the admin SPA's Quiz bank page (app/static/admin/sections/
quiz.js). Uses the shared app.admin_session cookie like every other section;
reads need require_admin, writes require_admin_write (CSRF header).

Separate section from quiz_admin.py deliberately, same reason poll/quiz stay
separate: that page reviews one AI-drafted day; this one manages a standing
bank of hand-entered questions that generate_quiz() falls back to when
Claude's draft fails validation (see services/daily_games.py:bank_quiz_
questions).

The listing replaces the old session-gated GET /admin/quiz-bank/api/list
(nothing in the app or backend called it; readers never see the bank
directly, it only feeds generate_quiz()'s fallback tier). It keeps the
limit/offset/activeOnly paging for scripts, while the SPA just asks for
everything and lets the table sort and search.
"""
from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_session import require_admin, require_admin_write
from app.database import get_db
from app.models import QuizBankQuestion
from app.services.daily_games import BANK_QUIZ_SYSTEM, validate_quiz_question
from app.services.llm_gen import call_claude_json

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/api/quiz-bank", dependencies=[Depends(require_admin)])
OPTION_COUNT = 4
MAX_LIMIT = 2000


def _question_json(q: QuizBankQuestion) -> dict:
    return {
        "id": q.id,
        "question": q.question,
        "options": q.options,
        "correctIndex": q.correct_index,
        "explanation": q.explanation,
        "category": q.category,
        "isActive": q.is_active,
        "usedCount": q.used_count,
        "createdAt": q.created_at.isoformat() if q.created_at else None,
        "lastUsedAt": q.last_used_at.isoformat() if q.last_used_at else None,
    }


@router.get("")
async def list_questions(
    limit: int = Query(MAX_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
    activeOnly: bool = False,
    db: AsyncSession = Depends(get_db),
):
    total = (await db.execute(select(func.count()).select_from(QuizBankQuestion))).scalar_one()
    active_total = (await db.execute(
        select(func.count()).where(QuizBankQuestion.is_active.is_(True))
    )).scalar_one()
    categories = (await db.execute(
        select(QuizBankQuestion.category).where(QuizBankQuestion.category.is_not(None))
        .distinct().order_by(QuizBankQuestion.category)
    )).scalars().all()

    base = select(QuizBankQuestion)
    if activeOnly:
        base = base.where(QuizBankQuestion.is_active.is_(True))
    matching = active_total if activeOnly else total
    rows = (await db.execute(
        base.order_by(QuizBankQuestion.created_at.desc(), QuizBankQuestion.id.desc())
        .offset(offset).limit(limit)
    )).scalars().all()

    return {
        "counts": {"total": total, "active": active_total, "inactive": total - active_total},
        "categories": [c for c in categories if c],
        "optionCount": OPTION_COUNT,
        "limit": limit,
        "offset": offset,
        "hasMore": offset + len(rows) < matching,
        "items": [_question_json(q) for q in rows],
    }


class GenerateIn(BaseModel):
    category: str = ""


@router.post("/generate", dependencies=[Depends(require_admin_write)])
async def generate(body: GenerateIn):
    """Draft one question with Claude and hand it back to the add-question
    form for review — never saved directly. Follows
    [[llm-generation-human-review-gate]]: generate greedily, let the
    human reviewer be the only gate before it's active in the bank."""
    category = (body.category or "").strip()
    user_content = (
        f"Write a question about: {category}." if category
        else "Write a question on any general-knowledge topic."
    )
    payload = await call_claude_json(BANK_QUIZ_SYSTEM, user_content, max_tokens=500)
    if payload is None:
        raise HTTPException(status_code=502, detail="Claude request failed. Try again.")
    try:
        draft = validate_quiz_question(payload)
    except Exception as exc:
        logger.warning("Bank quiz generation failed validation: %s", exc)
        raise HTTPException(status_code=422, detail=f"Claude's draft failed validation ({exc}). Try again.")
    return {
        "draft": {
            "question": draft["question"],
            "options": draft["options"],
            "correctIndex": draft["correct_index"],
            "explanation": draft["explanation"],
            "category": category or None,
        },
    }


class AddIn(BaseModel):
    question: str = ""
    options: list[str] = Field(default_factory=list)
    correctIndex: int = 0
    explanation: str = ""
    category: str | None = None


@router.post("", dependencies=[Depends(require_admin_write)])
async def add(body: AddIn, db: AsyncSession = Depends(get_db)):
    question = (body.question or "").strip()
    options = [(option or "").strip() for option in body.options]
    if len(options) > OPTION_COUNT:
        raise HTTPException(status_code=400, detail=f"A question has exactly {OPTION_COUNT} options.")
    options += [""] * (OPTION_COUNT - len(options))
    explanation = (body.explanation or "").strip()
    category = (body.category or "").strip() or None
    correct_index = body.correctIndex

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
        raise HTTPException(status_code=400, detail=error)

    item = QuizBankQuestion(
        question=question, options=options, correct_index=correct_index,
        explanation=explanation, category=category,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return _question_json(item)


class BulkIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=500)
    action: Literal["activate", "deactivate", "delete"]


@router.post("/bulk", dependencies=[Depends(require_admin_write)])
async def bulk(body: BulkIn, db: AsyncSession = Depends(get_db)):
    questions = (await db.execute(
        select(QuizBankQuestion).where(QuizBankQuestion.id.in_(body.ids)))).scalars().all()
    for question in questions:
        if body.action == "delete":
            await db.delete(question)
        else:
            question.is_active = body.action == "activate"
    await db.commit()
    return {"ok": True, "updated": len(questions)}


@router.post("/{question_id}/toggle", dependencies=[Depends(require_admin_write)])
async def toggle(question_id: int, db: AsyncSession = Depends(get_db)):
    question = await db.get(QuizBankQuestion, question_id)
    if question is None:
        raise HTTPException(status_code=404, detail="Question not found")
    question.is_active = not question.is_active
    await db.commit()
    return _question_json(question)


@router.delete("/{question_id}", dependencies=[Depends(require_admin_write)])
async def delete(question_id: int, db: AsyncSession = Depends(get_db)):
    question = await db.get(QuizBankQuestion, question_id)
    if question is None:
        raise HTTPException(status_code=404, detail="Question not found")
    await db.delete(question)
    await db.commit()
    return {"ok": True, "id": question_id}
