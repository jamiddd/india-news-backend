"""Human review gate for the AI-drafted Daily Quiz.

The page is deliberately its own thing rather than a generalisation of
poll_admin.py: a quiz is 5 questions x 4 options where a poll is one question
plus context, and sharing the form rendering would mean parameterising nearly
every line. Sign-in *is* shared — see app/admin_session.py — so one login and
one notification cover both reviews.

A quiz is not a set-filtering problem with a verifiable answer, so nothing here
can check Claude's facts automatically. That is the whole point of the gate:
the reviewer is the filter, and Regenerate is unlimited because redrafting
costs Claude tokens only — no APIVerve credits.
"""
from datetime import datetime
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import html

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
from app.models import DailyQuiz, utc_now
from app.services.daily_games import IST, generate_quiz, quiz_publish_at

router = APIRouter(prefix="/admin/quiz")
TITLE = "Daily Quiz Review"

QUESTION_COUNT = 5
OPTION_COUNT = 4


def _error_page(csrf: str, message: str) -> HTMLResponse:
    return layout(TITLE, 
        f"<h1>Daily Quiz</h1><p class=danger>Draft generation failed: {html.escape(message)}</p>"
        f"<form method=post action='/admin/quiz/generate'><input type=hidden name=csrf value='{csrf}'>"
        f"<button>Try again</button></form>")


@router.get("/login", response_class=HTMLResponse)
async def login_page():
    return login_form(TITLE, "/admin/quiz/login")


@router.post("/login")
async def login(request: Request):
    fields = await form_fields(request)
    if not credentials_match(fields):
        return layout(TITLE, "<h1>Sign in failed</h1><p class=danger>Invalid credentials.</p><a href='/admin/quiz/login'>Try again</a>")
    response = RedirectResponse("/admin/quiz", status_code=303)
    set_session_cookie(response, request)
    return response


def _question_fieldset(index: int, question: dict) -> str:
    text = html.escape(str(question.get("question", "")))
    explanation = html.escape(str(question.get("explanation", "")))
    correct = question.get("correct_index", 0)
    options = list(question.get("options") or [])
    options += [""] * (OPTION_COUNT - len(options))
    rows = "".join(
        f"<label class=opt>"
        f"<input type=radio name=correct_{index} value={i}{' checked' if i == correct else ''}>"
        f"<input name=option_{index} value='{html.escape(str(options[i]), quote=True)}'>"
        f"</label>"
        for i in range(OPTION_COUNT)
    )
    return (
        f"<fieldset><legend>Question {index + 1}</legend>"
        f"<textarea name=question_{index} required>{text}</textarea>"
        f"<p class=meta>Select the correct answer.</p>{rows}"
        f"<label>Explanation<input name=explanation_{index} value='{explanation}'></label>"
        f"</fieldset>")


@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    csrf = session_csrf(request)
    if not csrf:
        return RedirectResponse("/admin/quiz/login", status_code=303)
    today = datetime.now(IST).date()
    quiz = await db.scalar(select(DailyQuiz).where(DailyQuiz.puzzle_date == today))
    if not quiz:
        return layout(TITLE, 
            f"<h1>Daily Quiz</h1><p>No quiz exists for {today}.</p>"
            f"<form method=post action='/admin/quiz/generate'><input type=hidden name=csrf value='{csrf}'>"
            f"<button>Generate draft</button></form>")

    fields = "".join(_question_fieldset(i, q) for i, q in enumerate(quiz.questions[:QUESTION_COUNT]))
    editable = quiz.status == "draft"
    if editable:
        controls = ("<button name=action value=approve>Approve and publish</button>"
                    "<button name=action value=regenerate>Regenerate</button>"
                    "<button name=action value=reject>Reject and use curated set</button>")
    else:
        controls = ("<p>This quiz is no longer a draft. "
                    "<button name=action value=regenerate>Regenerate a new draft</button></p>")
    served = "" if quiz.status == "approved" else (
        "<p class=meta>Readers are currently being served the curated fallback set, "
        "not this draft.</p>")
    return layout(TITLE, 
        f"<h1>Daily Quiz — {quiz.puzzle_date}</h1>"
        f"<p class=meta>Status: {quiz.status} · Source: {quiz.source}</p>{served}"
        f"<form method=post action='/admin/quiz/update'>"
        f"<input type=hidden name=csrf value='{csrf}'>"
        f"<input type=hidden name=quiz_id value='{quiz.id}'>{fields}{controls}</form>")


async def _redraft(db: AsyncSession, day) -> None:
    """Replace today's questions with a fresh Claude draft. Unlimited by
    design — the reviewer regenerates until satisfied, and it costs no
    APIVerve credits."""
    questions, source = await generate_quiz(day)
    quiz = await db.scalar(select(DailyQuiz).where(DailyQuiz.puzzle_date == day))
    if quiz is None:
        quiz = DailyQuiz(puzzle_date=day, questions=questions, source=source,
                         status="draft", publish_at=quiz_publish_at(day))
        db.add(quiz)
    else:
        quiz.questions, quiz.source = questions, source
        quiz.status, quiz.approved_at = "draft", None
    await db.commit()


@router.post("/generate")
async def generate(request: Request, db: AsyncSession = Depends(get_db)):
    fields = await form_fields(request)
    verify(request, fields)
    try:
        await _redraft(db, datetime.now(IST).date())
    except HTTPException:
        raise
    except Exception as exc:
        return _error_page(fields.get("csrf", ""), str(exc))
    return RedirectResponse("/admin/quiz", status_code=303)


def _read_edited_questions(raw: dict[str, list[str]]) -> list[dict]:
    """Rebuild the question list from the edited form.

    Validated the same way generated content is: the reviewer can introduce a
    blank question or a duplicate option just as easily as Claude can, and an
    approved quiz goes straight to readers with nothing downstream to catch it.
    """
    questions = []
    for index in range(QUESTION_COUNT):
        text = (raw.get(f"question_{index}", [""])[0] or "").strip()
        options = [option.strip() for option in raw.get(f"option_{index}", [])]
        explanation = (raw.get(f"explanation_{index}", [""])[0] or "").strip()
        try:
            correct = int(raw.get(f"correct_{index}", ["0"])[0])
        except ValueError:
            correct = 0
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


@router.post("/update")
async def update(request: Request, db: AsyncSession = Depends(get_db)):
    body = (await request.body()).decode()
    raw = parse_qs(body, keep_blank_values=True)
    fields = {key: values[-1] for key, values in raw.items()}
    verify(request, fields)
    today = datetime.now(IST).date()
    action = fields.get("action")

    if action == "regenerate":
        try:
            await _redraft(db, today)
        except HTTPException:
            raise
        except Exception as exc:
            return _error_page(fields.get("csrf", ""), str(exc))
        return RedirectResponse("/admin/quiz", status_code=303)

    quiz = await db.get(DailyQuiz, int(fields["quiz_id"]))
    if quiz is None:
        raise HTTPException(status_code=404, detail="Quiz not found")

    if action == "reject":
        if quiz.status != "draft":
            raise HTTPException(status_code=409, detail="Only a draft can be rejected")
        quiz.status = "rejected"
        await db.commit()
        return RedirectResponse("/admin/quiz", status_code=303)

    if quiz.status != "draft":
        raise HTTPException(status_code=409, detail="Only a draft can be approved")
    try:
        questions = _read_edited_questions(raw)
    except ValueError as exc:
        return layout(TITLE, 
            f"<h1>Daily Quiz</h1><p class=danger>{html.escape(str(exc))}</p>"
            f"<a href='/admin/quiz'>Back to the draft</a>")
    quiz.questions = questions
    quiz.status = "approved"
    quiz.approved_at = utc_now()
    quiz.publish_at = quiz.publish_at or quiz_publish_at(quiz.puzzle_date)
    await db.commit()
    return RedirectResponse("/admin/quiz", status_code=303)
