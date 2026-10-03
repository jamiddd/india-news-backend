"""Human review gate for the AI-drafted Daily Quiz.

JSON API for the admin SPA's Daily Quiz page (app/static/admin/sections/
quiz.js). Uses the shared app.admin_session cookie like every other section;
reads need require_admin, writes require_admin_write (CSRF header).

The section is deliberately its own thing rather than a generalisation of
poll_admin.py: a quiz is 5 questions x 4 options where a poll is one question
plus context, and sharing the editing logic would mean parameterising nearly
every line. Sign-in *is* shared — see app/admin_session.py — so one login and
one notification cover both reviews.

A quiz is not a set-filtering problem with a verifiable answer, so nothing here
can check Claude's facts automatically. That is the whole point of the gate:
the reviewer is the filter, and Regenerate is unlimited because redrafting
costs Claude tokens only — no APIVerve credits.
"""
from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_session import require_admin, require_admin_write
from app.database import get_db
from app.models import DailyQuiz, utc_now
from app.services.daily_games import IST, generate_quiz, quiz_publish_at

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/api/quiz", dependencies=[Depends(require_admin)])

QUESTION_COUNT = 5
OPTION_COUNT = 4


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _today():
    return datetime.now(IST).date()


def _quiz_json(quiz: DailyQuiz | None) -> dict:
    """Today's quiz as the SPA needs it. Questions are padded to four options
    so the editor always has four fields, exactly like the old form did."""
    payload = {
        "date": _today().isoformat(),
        "questionCount": QUESTION_COUNT,
        "optionCount": OPTION_COUNT,
        "quiz": None,
    }
    if quiz is None:
        return payload
    questions = []
    for index, q in enumerate((quiz.questions or [])[:QUESTION_COUNT]):
        options = [str(o) for o in (q.get("options") or [])][:OPTION_COUNT]
        options += [""] * (OPTION_COUNT - len(options))
        correct = q.get("correct_index", 0)
        questions.append({
            "id": q.get("id", index + 1),
            "question": str(q.get("question", "")),
            "options": options,
            "correctIndex": correct if isinstance(correct, int) else 0,
            "explanation": str(q.get("explanation", "") or ""),
        })
    payload["quiz"] = {
        "id": quiz.id,
        "puzzleDate": quiz.puzzle_date.isoformat(),
        "status": quiz.status,
        "source": quiz.source,
        "generatedAt": _iso(quiz.generated_at),
        "publishAt": _iso(quiz.publish_at),
        "approvedAt": _iso(quiz.approved_at),
        # Only a draft is editable; anything else can only be regenerated.
        "editable": quiz.status == "draft",
        # Readers get the curated fallback set until this quiz is approved.
        "servedToReaders": quiz.status == "approved",
        "questions": questions,
    }
    return payload


async def _today_quiz(db: AsyncSession) -> DailyQuiz | None:
    return await db.scalar(select(DailyQuiz).where(DailyQuiz.puzzle_date == _today()))


@router.get("")
async def today_quiz(db: AsyncSession = Depends(get_db)):
    return _quiz_json(await _today_quiz(db))


async def _redraft(db: AsyncSession, day) -> None:
    """Replace today's questions with a fresh Claude draft. Unlimited by
    design — the reviewer regenerates until satisfied, and it costs no
    APIVerve credits."""
    questions, source = await generate_quiz(day, db)
    quiz = await db.scalar(select(DailyQuiz).where(DailyQuiz.puzzle_date == day))
    if quiz is None:
        quiz = DailyQuiz(puzzle_date=day, questions=questions, source=source,
                         status="draft", publish_at=quiz_publish_at(day))
        db.add(quiz)
    else:
        quiz.questions, quiz.source = questions, source
        quiz.status, quiz.approved_at = "draft", None
    await db.commit()


@router.post("/generate", dependencies=[Depends(require_admin_write)])
async def generate(db: AsyncSession = Depends(get_db)):
    """Generate today's draft when none exists, or regenerate it. Regenerating
    an approved quiz takes it offline (readers get the curated set until the
    new draft is approved) — the SPA confirms that before calling."""
    day = _today()
    try:
        await _redraft(db, day)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Daily quiz draft generation failed: %s", exc)
        await db.rollback()
        raise HTTPException(status_code=502, detail=f"Draft generation failed: {exc}. Try again.")
    return _quiz_json(await _today_quiz(db))


class QuestionIn(BaseModel):
    question: str = ""
    options: list[str] = Field(default_factory=list)
    correctIndex: int = 0
    explanation: str = ""


class ApproveIn(BaseModel):
    questions: list[QuestionIn]


def _read_edited_questions(items: list[QuestionIn]) -> list[dict]:
    """Rebuild the question list from the reviewer's edits.

    Validated the same way generated content is: the reviewer can introduce a
    blank question or a duplicate option just as easily as Claude can, and an
    approved quiz goes straight to readers with nothing downstream to catch it.
    """
    if len(items) != QUESTION_COUNT:
        raise ValueError(f"A quiz needs exactly {QUESTION_COUNT} questions")
    questions = []
    for index, item in enumerate(items):
        text = (item.question or "").strip()
        options = [(option or "").strip() for option in item.options]
        explanation = (item.explanation or "").strip()
        correct = item.correctIndex
        if not text:
            raise ValueError(f"Question {index + 1} is empty")
        if len(options) != OPTION_COUNT or any(not option for option in options):
            raise ValueError(f"Question {index + 1} needs {OPTION_COUNT} non-empty options")
        if len({option.casefold() for option in options}) != OPTION_COUNT:
            raise ValueError(f"Question {index + 1} has duplicate options")
        if not 0 <= correct < OPTION_COUNT:
            raise ValueError(f"Question {index + 1} has no valid correct answer")
        questions.append({
            "id": index + 1,
            "question": text,
            "options": options,
            "correct_index": correct,
            "explanation": explanation,
        })
    return questions


@router.post("/{quiz_id}/approve", dependencies=[Depends(require_admin_write)])
async def approve(quiz_id: int, body: ApproveIn, db: AsyncSession = Depends(get_db)):
    """Save the reviewer's edits and publish. Edits are only ever saved by
    approving, the same as the old form."""
    quiz = await db.get(DailyQuiz, quiz_id)
    if quiz is None:
        raise HTTPException(status_code=404, detail="Quiz not found")
    if quiz.status != "draft":
        raise HTTPException(status_code=409, detail="Only a draft can be approved")
    try:
        questions = _read_edited_questions(body.questions)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    quiz.questions = questions
    quiz.status = "approved"
    quiz.approved_at = utc_now()
    quiz.publish_at = quiz.publish_at or quiz_publish_at(quiz.puzzle_date)
    await db.commit()
    return _quiz_json(await _today_quiz(db))


@router.post("/{quiz_id}/reject", dependencies=[Depends(require_admin_write)])
async def reject(quiz_id: int, db: AsyncSession = Depends(get_db)):
    """Reject the draft; readers keep getting the curated fallback set."""
    quiz = await db.get(DailyQuiz, quiz_id)
    if quiz is None:
        raise HTTPException(status_code=404, detail="Quiz not found")
    if quiz.status != "draft":
        raise HTTPException(status_code=409, detail="Only a draft can be rejected")
    quiz.status = "rejected"
    await db.commit()
    return _quiz_json(await _today_quiz(db))
