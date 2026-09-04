"""Human review gate for the AI-drafted Daily Quiz.

Deliberately a near-copy of poll_admin.py rather than a shared abstraction:
the two differ in the shape of the thing being edited (5 questions x 4 options
vs. one question + context) and sharing the layout would mean parameterising
almost every line. Session/CSRF handling is identical on purpose — it reuses
the same POLL_ADMIN_* credentials, so there is one admin login, not two.

A quiz is not a set-filtering problem with a verifiable answer, so nothing here
can check Claude's facts automatically. That is the whole point of the gate:
the reviewer is the filter, and Regenerate is unlimited because redrafting
costs Claude tokens only — no APIVerve credits.
"""
import base64
import hashlib
import hmac
import html
import secrets
import time
from datetime import datetime
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import DailyQuiz
from app.models import utc_now
from app.services.daily_games import IST, generate_quiz, quiz_publish_at

router = APIRouter(prefix="/admin/quiz")

QUESTION_COUNT = 5
OPTION_COUNT = 4


def _secret() -> bytes:
    if not settings.POLL_SESSION_SECRET or not settings.POLL_ADMIN_PASSWORD:
        raise HTTPException(status_code=503, detail="Quiz admin is not configured")
    return settings.POLL_SESSION_SECRET.encode()


def _make_session() -> str:
    payload = f"{int(time.time()) + 8 * 3600}:{secrets.token_urlsafe(24)}"
    signature = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}:{signature}".encode()).decode()


def _session(request: Request) -> str | None:
    try:
        decoded = base64.urlsafe_b64decode(request.cookies.get("quiz_admin", "")).decode()
        expiry, csrf, signature = decoded.split(":", 2)
        payload = f"{expiry}:{csrf}"
        expected = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
        return csrf if int(expiry) > int(time.time()) and hmac.compare_digest(signature, expected) else None
    except Exception:
        return None


async def _fields(request: Request) -> dict[str, str]:
    parsed = parse_qs((await request.body()).decode(), keep_blank_values=True)
    return {key: values[-1] for key, values in parsed.items()}


def _layout(body: str) -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html><html><head><meta name=viewport content='width=device-width'><title>Daily Quiz Admin</title><style>
    body{{font-family:system-ui;max-width:900px;margin:40px auto;padding:0 20px;background:#f6f6f6;color:#171717}}main{{background:white;padding:24px;border-radius:16px}}input,textarea{{box-sizing:border-box;width:100%;padding:10px;margin:5px 0 12px}}button{{padding:10px 16px;margin-right:8px}}.meta{{color:#666}}.danger{{color:#a00}}fieldset{{border:1px solid #ddd;border-radius:12px;margin:0 0 20px;padding:16px}}legend{{padding:0 6px;color:#666}}label.opt{{display:flex;align-items:center;gap:8px}}label.opt input[type=radio]{{width:auto;margin:0}}</style></head><body><main>{body}</main></body></html>""")


def _verify(request: Request, fields: dict[str, str]) -> None:
    csrf = _session(request)
    if not csrf or not hmac.compare_digest(csrf, fields.get("csrf", "")):
        raise HTTPException(status_code=403, detail="Invalid session or CSRF token")


def _error_page(csrf: str, message: str) -> HTMLResponse:
    return _layout(
        f"<h1>Daily Quiz</h1><p class=danger>Draft generation failed: {html.escape(message)}</p>"
        f"<form method=post action='/admin/quiz/generate'><input type=hidden name=csrf value='{csrf}'>"
        f"<button>Try again</button></form>")


@router.get("/login", response_class=HTMLResponse)
async def login_page():
    return _layout("<h1>Daily Quiz Admin</h1><form method=post><label>Username<input name=username required></label><label>Password<input name=password type=password required></label><button>Sign in</button></form>")


@router.post("/login")
async def login(request: Request):
    fields = await _fields(request)
    valid_user = hmac.compare_digest(fields.get("username", ""), settings.POLL_ADMIN_USERNAME)
    valid_password = settings.POLL_ADMIN_PASSWORD and hmac.compare_digest(fields.get("password", ""), settings.POLL_ADMIN_PASSWORD)
    if not valid_user or not valid_password:
        return _layout("<h1>Sign in failed</h1><p class=danger>Invalid credentials.</p><a href='/admin/quiz/login'>Try again</a>")
    response = RedirectResponse("/admin/quiz", status_code=303)
    response.set_cookie("quiz_admin", _make_session(), httponly=True, secure=request.url.scheme == "https", samesite="strict", max_age=8 * 3600)
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
    csrf = _session(request)
    if not csrf:
        return RedirectResponse("/admin/quiz/login", status_code=303)
    today = datetime.now(IST).date()
    quiz = await db.scalar(select(DailyQuiz).where(DailyQuiz.puzzle_date == today))
    if not quiz:
        return _layout(
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
    return _layout(
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
    fields = await _fields(request)
    _verify(request, fields)
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
    _verify(request, fields)
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
        return _layout(
            f"<h1>Daily Quiz</h1><p class=danger>{html.escape(str(exc))}</p>"
            f"<a href='/admin/quiz'>Back to the draft</a>")
    quiz.questions = questions
    quiz.status = "approved"
    quiz.approved_at = utc_now()
    quiz.publish_at = quiz.publish_at or quiz_publish_at(quiz.puzzle_date)
    await db.commit()
    return RedirectResponse("/admin/quiz", status_code=303)
