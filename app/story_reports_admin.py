import base64
import hashlib
import hmac
import html
import secrets
import time
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import StoryReport

router = APIRouter(prefix="/admin/reports")

REASON_LABELS = {
    "misleading": "Misleading / Clickbait",
    "factually_incorrect": "Factually incorrect",
    "offensive": "Offensive / Inappropriate",
    "duplicate_spam": "Duplicate / Spam",
    "other": "Other",
}


# Same signed-cookie session mechanism as app.poll_admin — one admin login
# for both pages.
def _secret() -> bytes:
    if not settings.POLL_SESSION_SECRET or not settings.POLL_ADMIN_PASSWORD:
        raise HTTPException(status_code=503, detail="Reports admin is not configured")
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
    return HTMLResponse(f"""<!doctype html><html><head><meta name=viewport content='width=device-width'><title>Story Reports Admin</title><style>
    body{{font-family:system-ui;max-width:960px;margin:40px auto;padding:0 20px;background:#f6f6f6;color:#171717}}main{{background:white;padding:24px;border-radius:16px}}input,textarea{{box-sizing:border-box;width:100%;padding:10px;margin:5px 0 12px}}button{{padding:8px 14px;margin-right:8px}}.meta{{color:#666}}.danger{{color:#a00}}.report{{border-bottom:1px solid #eee;padding:16px 0}}.report:last-child{{border-bottom:none}}</style></head><body><main>{body}</main></body></html>""")


@router.get("/login", response_class=HTMLResponse)
async def login_page():
    return _layout("<h1>Story Reports Admin</h1><form method=post><label>Username<input name=username required></label><label>Password<input name=password type=password required></label><button>Sign in</button></form>")


@router.post("/login")
async def login(request: Request):
    fields = await _fields(request)
    valid_user = hmac.compare_digest(fields.get("username", ""), settings.POLL_ADMIN_USERNAME)
    valid_password = settings.POLL_ADMIN_PASSWORD and hmac.compare_digest(fields.get("password", ""), settings.POLL_ADMIN_PASSWORD)
    if not valid_user or not valid_password:
        return _layout("<h1>Sign in failed</h1><p class=danger>Invalid credentials.</p><a href='/admin/reports/login'>Try again</a>")
    response = RedirectResponse("/admin/reports", status_code=303)
    response.set_cookie("poll_admin", _make_session(), httponly=True, secure=request.url.scheme == "https", samesite="strict", max_age=8 * 3600)
    return response


def _verify(request: Request, fields: dict[str, str]) -> None:
    csrf = _session(request)
    if not csrf or not hmac.compare_digest(csrf, fields.get("csrf", "")):
        raise HTTPException(status_code=403, detail="Invalid session or CSRF token")


@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    csrf = _session(request)
    if not csrf:
        return RedirectResponse("/admin/reports/login", status_code=303)

    reports = (await db.execute(
        select(StoryReport).where(StoryReport.status == "open").order_by(StoryReport.created_at.desc())
    )).scalars().all()

    if not reports:
        return _layout("<h1>Story Reports</h1><p>No open reports.</p>")

    rows = []
    for report in reports:
        reason_text = html.escape(REASON_LABELS.get(report.reason, report.reason))
        note_text = f"<p>{html.escape(report.note)}</p>" if report.note else ""
        source = (
            f"<a target=_blank href='/api/v1/clusters/{report.cluster_id}'>cluster {report.cluster_id}</a>"
            if report.cluster_id else "cluster deleted"
        )
        rows.append(
            f"<div class=report><p class=meta>{report.created_at} · reported by {html.escape(report.user_id)} · {source}</p>"
            f"<p><b>{reason_text}</b></p>{note_text}"
            f"<form method=post action='/admin/reports/update'><input type=hidden name=csrf value='{csrf}'>"
            f"<input type=hidden name=report_id value='{report.id}'>"
            f"<button name=action value=reviewed>Mark reviewed</button>"
            f"<button name=action value=dismissed>Dismiss</button></form></div>"
        )
    return _layout(f"<h1>Story Reports</h1>{''.join(rows)}")


@router.post("/update")
async def update(request: Request, db: AsyncSession = Depends(get_db)):
    fields = await _fields(request)
    _verify(request, fields)
    report = await db.get(StoryReport, int(fields["report_id"]))
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    action = fields.get("action")
    if action not in ("reviewed", "dismissed"):
        raise HTTPException(status_code=400, detail="Invalid action")
    report.status = action
    await db.commit()
    return RedirectResponse("/admin/reports", status_code=303)
