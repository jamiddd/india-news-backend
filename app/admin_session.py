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


# Same colour/type tokens as app/static/site.css, kept to the handful this
# page actually uses — a reader never sees this page, but it should still
# look like it belongs to the same product, not a bare framework default.
STYLE = """
    :root{
      --ink:#171717; --ink-2:#4a4a4a; --ink-3:#767676;
      --bg:#ffffff; --surface:#F3F3F8; --line:#e4e4ea;
      --accent:#1976D2; --on-accent:#FFFFFF; --danger:#C62828; --done:#2E7D32;
      --serif:"Source Serif 4",Georgia,"Times New Roman",serif;
      --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
      --radius:14px;
    }
    @media (prefers-color-scheme:dark){ :root{
      --ink:#E8E8E8; --ink-2:#b6b6b6; --ink-3:#8d8d8d;
      --bg:#121212; --surface:#1E1E24; --line:#2c2c34;
      --accent:#64B5F6; --on-accent:#171717; --danger:#EF5350; --done:#66BB6A;
    }}
    *{box-sizing:border-box}
    body{margin:0;background:var(--surface);color:var(--ink);font-family:var(--sans);line-height:1.55}
    h1,h2{font-family:var(--serif);font-weight:700;letter-spacing:-0.01em;margin:0}
    h1{font-size:1.5rem}
    h2{font-size:1.05rem;margin:0 0 4px}
    a{color:var(--accent);text-decoration:none}
    a:hover{text-decoration:underline}
    header.bar{
      background:var(--bg);border-bottom:1px solid var(--line);
      padding:14px 24px;display:flex;align-items:center;gap:10px;
    }
    header.bar .brand{display:flex;align-items:center;gap:10px}
    header.bar .brand:hover{text-decoration:none}
    header.bar .mark{width:22px;height:22px;flex:0 0 auto;border-radius:50%}
    header.bar .wordmark{font-family:var(--serif);font-weight:700;font-size:16px;color:var(--ink)}
    header.bar .wordmark .stop{color:var(--accent)}
    header.bar .tag{font-size:11px;color:var(--ink-3);border-left:1px solid var(--line);padding-left:10px;margin-left:2px}
    .wrap{max-width:920px;margin:0 auto;padding:28px 20px 60px}
    main{background:var(--bg);border:1px solid var(--line);border-radius:var(--radius);padding:24px}
    input,textarea{
      box-sizing:border-box;width:100%;padding:10px 12px;margin:5px 0 12px;
      border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink);
      font:inherit;
    }
    button{
      padding:9px 16px;margin-right:8px;border:1px solid var(--line);border-radius:8px;
      background:var(--surface);color:var(--ink);font:inherit;cursor:pointer;
    }
    button:hover{border-color:var(--accent)}
    .meta{color:var(--ink-3);font-size:.92em}
    .danger{color:var(--danger)}
    .done{color:var(--done)}
    fieldset{border:1px solid var(--line);border-radius:12px;margin:0 0 20px;padding:16px}
    legend{padding:0 6px;color:var(--ink-3)}
    label.opt{display:flex;align-items:center;gap:8px}
    label.opt input[type=radio]{width:auto;margin:0}
    .task{border:1px solid var(--line);border-radius:12px;padding:16px;margin-bottom:12px}
    .report{border-bottom:1px solid var(--line);padding:16px 0}
    .report:last-child{border-bottom:none}
    table{border-collapse:collapse}
    th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;font-size:.94em}
    th{color:var(--ink-3);font-weight:600;font-size:.82em;text-transform:uppercase;letter-spacing:.04em}
    nav.admin-nav{display:flex;flex-wrap:wrap;gap:4px 14px;margin:2px 0 22px;font-size:.92em}
    nav.admin-nav a{color:var(--ink-2)}
    nav.admin-nav b{color:var(--ink);border-bottom:2px solid var(--accent);padding-bottom:2px}
"""

# The app's own OIN mark (see app/static/home.html), inlined so the admin
# masthead reads as the same product without a fetched image.
MARK_SVG = (
    "<svg class=mark viewBox='0 0 512 512' role=img aria-label='Open Indian News'>"
    "<defs><clipPath id=oin><circle cx=256 cy=256 r=248/></clipPath></defs>"
    "<circle cx=256 cy=256 r=248 fill='#1B3A66'/>"
    "<g clip-path='url(#oin)' fill='#F4F0E4'>"
    "<path fill-rule=evenodd d='M95,96 C30,96 0,146 0,256 C0,366 30,416 95,416 C160,416 190,366 190,256 "
    "C190,146 160,96 95,96 Z M95,168 C114,168 120,193 120,256 C120,319 114,344 95,344 C76,344 70,319 70,256 "
    "C70,193 76,168 95,168 Z'/>"
    "<path d='M206,96 H286 V416 H206 Z'/>"
    "<path d='M302,96 H374 V416 H302 Z M440,96 H512 V416 H440 Z M302,96 H374 L512,416 H440 Z'/>"
    "</g></svg>"
)

NAV = [
    ("/admin", "Daily review"),
    ("/admin/polls", "Polls"),
    ("/admin/quiz", "Quiz"),
    ("/admin/quiz-bank", "Quiz bank"),
    ("/admin/feedback", "Feedback"),
    ("/admin/reports", "Story reports"),
    ("/admin/donations", "Donations"),
    ("/admin/users", "Users"),
    ("/admin/timelines", "Timelines"),
]


def nav(current: str) -> str:
    """The cross-page nav bar. `current` is the href of the page rendering
    it, so that page shows as bold rather than a link to itself."""
    links = "".join(
        f"<b>{label}</b>" if href == current else f"<a href='{href}'>{label}</a>"
        for href, label in NAV)
    return f"<nav class=admin-nav>{links}</nav>"


def layout(title: str, body: str) -> HTMLResponse:
    header = (
        "<header class=bar><a class=brand href='/'>"
        f"{MARK_SVG}<span class=wordmark>Open Indian News<span class=stop>.</span></span>"
        "</a><span class=tag>Admin</span></header>"
    )
    return HTMLResponse(
        f"<!doctype html><html><head><meta name=viewport content='width=device-width'>"
        f"<title>{title}</title><style>{STYLE}</style></head>"
        f"<body>{header}<div class=wrap><main>{body}</main></div></body></html>")


def login_form(title: str, action: str) -> HTMLResponse:
    return layout(title, (
        f"<h1>{title}</h1><form method=post action='{action}'>"
        "<label>Username<input name=username required></label>"
        "<label>Password<input name=password type=password required></label>"
        "<button>Sign in</button></form>"))
