"""Shared sign-in for the admin review pages.

The poll and quiz reviewers are the same person doing the same job at the same
time of day, off the same credentials — so this is one session, not two. Before
this module each page minted its own cookie (`poll_admin`, `quiz_admin`) from
the same secret and the same POLL_ADMIN_* credentials, which meant signing in
twice to act on one notification.

Only the session/CSRF plumbing lives here. The pages themselves stay separate:
a poll is one question plus context, a quiz is five questions with four options
each, and sharing the form rendering would mean parameterising nearly every
line for no gain.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from urllib.parse import parse_qs

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

from app.config import settings

COOKIE_NAME = "oin_admin"
SESSION_HOURS = 8


def secret() -> bytes:
    if not settings.POLL_SESSION_SECRET or not settings.POLL_ADMIN_PASSWORD:
        raise HTTPException(status_code=503, detail="Admin review is not configured")
    return settings.POLL_SESSION_SECRET.encode()


def make_session() -> str:
    payload = f"{int(time.time()) + SESSION_HOURS * 3600}:{secrets.token_urlsafe(24)}"
    signature = hmac.new(secret(), payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}:{signature}".encode()).decode()


def session_csrf(request: Request) -> str | None:
    """The CSRF token for the current session, or None if there isn't a valid
    one. Doubles as the "is signed in" check."""
    try:
        decoded = base64.urlsafe_b64decode(request.cookies.get(COOKIE_NAME, "")).decode()
        expiry, csrf, signature = decoded.split(":", 2)
        payload = f"{expiry}:{csrf}"
        expected = hmac.new(secret(), payload.encode(), hashlib.sha256).hexdigest()
        return csrf if int(expiry) > int(time.time()) and hmac.compare_digest(signature, expected) else None
    except Exception:
        return None


def credentials_match(fields: dict[str, str]) -> bool:
    valid_user = hmac.compare_digest(fields.get("username", ""), settings.POLL_ADMIN_USERNAME)
    valid_password = bool(settings.POLL_ADMIN_PASSWORD) and hmac.compare_digest(
        fields.get("password", ""), settings.POLL_ADMIN_PASSWORD)
    return valid_user and valid_password


def set_session_cookie(response, request: Request) -> None:
    response.set_cookie(
        COOKIE_NAME, make_session(), httponly=True,
        secure=request.url.scheme == "https", samesite="strict",
        max_age=SESSION_HOURS * 3600,
    )


async def form_fields(request: Request) -> dict[str, str]:
    parsed = parse_qs((await request.body()).decode(), keep_blank_values=True)
    return {key: values[-1] for key, values in parsed.items()}


def verify(request: Request, fields: dict[str, str]) -> None:
    csrf = session_csrf(request)
    if not csrf or not hmac.compare_digest(csrf, fields.get("csrf", "")):
        raise HTTPException(status_code=403, detail="Invalid session or CSRF token")


STYLE = """
    body{font-family:system-ui;max-width:900px;margin:40px auto;padding:0 20px;background:#f6f6f6;color:#171717}
    main{background:white;padding:24px;border-radius:16px}
    input,textarea{box-sizing:border-box;width:100%;padding:10px;margin:5px 0 12px}
    button{padding:10px 16px;margin-right:8px}
    a{color:#12507b}
    .meta{color:#666}.danger{color:#a00}.done{color:#15703a}
    fieldset{border:1px solid #ddd;border-radius:12px;margin:0 0 20px;padding:16px}
    legend{padding:0 6px;color:#666}
    label.opt{display:flex;align-items:center;gap:8px}
    label.opt input[type=radio]{width:auto;margin:0}
    .task{border:1px solid #ddd;border-radius:12px;padding:16px;margin-bottom:12px}
    .task h2{margin:0 0 4px;font-size:1.05rem}
"""


def layout(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><html><head><meta name=viewport content='width=device-width'>"
        f"<title>{title}</title><style>{STYLE}</style></head><body><main>{body}</main></body></html>")


def login_form(title: str, action: str) -> HTMLResponse:
    return layout(title, (
        f"<h1>{title}</h1><form method=post action='{action}'>"
        "<label>Username<input name=username required></label>"
        "<label>Password<input name=password type=password required></label>"
        "<button>Sign in</button></form>"))
