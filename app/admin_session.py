"""Shared sign-in for the admin.

Every admin section is the same person doing related jobs off the same
credentials, so this is one session, not one per page. Before this module
each page minted its own cookie (`poll_admin`, `quiz_admin`) from the same
secret and the same POLL_ADMIN_* credentials, which meant signing in twice to
act on one notification.

The admin is a single-page app (app/static/admin/, served by app/admin_spa.py)
over a JSON API (app/admin_api.py plus one /admin/api/<section> router per
section). Only the session/CSRF plumbing lives here; the server-rendered HTML
layout that used to live here went with the old pages.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time

from fastapi import HTTPException, Request

from app.config import settings

COOKIE_NAME = "oin_admin"
SESSION_HOURS = 8
ADMIN_PUBLIC_ORIGIN = "https://admin.openindiannews.com"


def admin_public_path(path: str) -> str:
    """Convert an internal /admin route to its public subdomain path."""
    if path != "/admin" and not path.startswith("/admin/"):
        return path
    public = path[len("/admin"):]
    return public or "/"


def admin_url(path: str) -> str:
    """Return the canonical public URL for an internal admin route."""
    return f"{ADMIN_PUBLIC_ORIGIN}{admin_public_path(path)}"


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


# Dependencies for the JSON API behind the admin SPA (app/admin_api.py and
# each section's /admin/api/<section> router). Reads only need the session
# cookie; anything that changes state also needs the session's CSRF token
# echoed in the X-CSRF-Token header, which a cross-site form or <img> cannot
# set. The cookie is SameSite=Strict as well, so this is belt and braces.
def require_admin(request: Request) -> str:
    csrf = session_csrf(request)
    if not csrf:
        raise HTTPException(status_code=401, detail="Sign in to continue")
    return csrf


def require_admin_write(request: Request) -> str:
    csrf = require_admin(request)
    if not hmac.compare_digest(csrf, request.headers.get("x-csrf-token", "")):
        raise HTTPException(status_code=403, detail="Invalid session or CSRF token")
    return csrf
