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
from app.models import DailyPoll, PollOption
from app.services.polls import IST, approve_poll, generate_draft

router = APIRouter(prefix="/admin/polls")


def _secret() -> bytes:
    if not settings.POLL_SESSION_SECRET or not settings.POLL_ADMIN_PASSWORD:
        raise HTTPException(status_code=503, detail="Poll admin is not configured")
    return settings.POLL_SESSION_SECRET.encode()


def _make_session() -> str:
    payload = f"{int(time.time()) + 8 * 3600}:{secrets.token_urlsafe(24)}"
    signature = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}:{signature}".encode()).decode()


def _session(request: Request) -> str | None:
    try:
        decoded = base64.urlsafe_b64decode(request.cookies.get("poll_admin", "")).decode()
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
    return HTMLResponse(f"""<!doctype html><html><head><meta name=viewport content='width=device-width'><title>Daily Poll Admin</title><style>
    body{{font-family:system-ui;max-width:860px;margin:40px auto;padding:0 20px;background:#f6f6f6;color:#171717}}main{{background:white;padding:24px;border-radius:16px}}input,textarea{{box-sizing:border-box;width:100%;padding:10px;margin:5px 0 12px}}button{{padding:10px 16px;margin-right:8px}}.meta{{color:#666}}.danger{{color:#a00}}</style></head><body><main>{body}</main></body></html>""")


@router.get("/login", response_class=HTMLResponse)
async def login_page():
    return _layout("<h1>Daily Poll Admin</h1><form method=post><label>Username<input name=username required></label><label>Password<input name=password type=password required></label><button>Sign in</button></form>")


@router.post("/login")
async def login(request: Request):
    fields = await _fields(request)
    valid_user = hmac.compare_digest(fields.get("username", ""), settings.POLL_ADMIN_USERNAME)
    valid_password = settings.POLL_ADMIN_PASSWORD and hmac.compare_digest(fields.get("password", ""), settings.POLL_ADMIN_PASSWORD)
    if not valid_user or not valid_password:
        return _layout("<h1>Sign in failed</h1><p class=danger>Invalid credentials.</p><a href='/admin/polls/login'>Try again</a>")
    response = RedirectResponse("/admin/polls", status_code=303)
    response.set_cookie("poll_admin", _make_session(), httponly=True, secure=request.url.scheme == "https", samesite="strict", max_age=8 * 3600)
    return response


@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    csrf = _session(request)
    if not csrf: return RedirectResponse("/admin/polls/login", status_code=303)
    poll = await db.scalar(select(DailyPoll).where(DailyPoll.poll_date == datetime.now(IST).date()))
    if not poll:
        return _layout(f"<h1>Daily Poll</h1><p>No draft exists for today.</p><form method=post action='/admin/polls/generate'><input type=hidden name=csrf value='{csrf}'><button>Generate draft</button></form>")
    options = (await db.execute(select(PollOption).where(PollOption.poll_id == poll.id).order_by(PollOption.position))).scalars().all()
    option_inputs = "".join(f"<label>Option {i+1}<input name=option value='{html.escape(option.text, quote=True)}'></label>" for i, option in enumerate(options))
    source_text = html.escape(poll.source_headline or "Evergreen fallback")
    source = f"<p><b>Source:</b> <a target=_blank href='/api/v1/clusters/{poll.source_cluster_id}'>{source_text}</a></p>" if poll.source_cluster_id else f"<p><b>Source:</b> {source_text}</p>"
    editable = poll.status == "draft" and datetime.now(IST) < poll.publish_at
    controls = "<button name=action value=approve>Approve for 9:00 AM</button><button name=action value=regenerate>Regenerate</button><button name=action value=reject>Reject and use fallback</button>" if editable else "<p>This poll can no longer be edited.</p>"
    return _layout(f"<h1>Daily Poll — {poll.poll_date}</h1><p class=meta>Status: {poll.status} · Publishes 9:00 AM IST</p>{source}<form method=post action='/admin/polls/update'><input type=hidden name=csrf value='{csrf}'><input type=hidden name=poll_id value='{poll.id}'><label>Question<textarea name=question required>{html.escape(poll.question)}</textarea></label><label>Context<textarea name=context required>{html.escape(poll.context)}</textarea></label>{option_inputs}{controls}</form>")


def _verify(request: Request, fields: dict[str, str]) -> None:
    csrf = _session(request)
    if not csrf or not hmac.compare_digest(csrf, fields.get("csrf", "")):
        raise HTTPException(status_code=403, detail="Invalid session or CSRF token")


def _error_page(csrf: str, message: str) -> HTMLResponse:
    return _layout(f"<h1>Daily Poll</h1><p class=danger>Draft generation failed: {html.escape(message)}</p><form method=post action='/admin/polls/generate'><input type=hidden name=csrf value='{csrf}'><button>Try again</button></form>")


@router.post("/generate")
async def generate(request: Request, db: AsyncSession = Depends(get_db)):
    fields = await _fields(request); _verify(request, fields)
    try:
        await generate_draft(db, datetime.now(IST).date())
    except HTTPException:
        raise
    except Exception as exc:
        return _error_page(fields.get("csrf", ""), str(exc))
    return RedirectResponse("/admin/polls", status_code=303)


@router.post("/update")
async def update(request: Request, db: AsyncSession = Depends(get_db)):
    fields = await _fields(request); _verify(request, fields)
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
