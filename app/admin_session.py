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
    @media (prefers-color-scheme:dark){ :root:not([data-theme=light]){
      --ink:#E8E8E8; --ink-2:#b6b6b6; --ink-3:#8d8d8d;
      --bg:#121212; --surface:#1E1E24; --line:#2c2c34;
      --accent:#64B5F6; --on-accent:#171717; --danger:#EF5350; --done:#66BB6A;
    }}
    :root[data-theme=dark]{
      --ink:#E8E8E8; --ink-2:#b6b6b6; --ink-3:#8d8d8d;
      --bg:#121212; --surface:#1E1E24; --line:#2c2c34;
      --accent:#64B5F6; --on-accent:#171717; --danger:#EF5350; --done:#66BB6A;
    }
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
    header.bar .mark{width:32px;height:32px;flex:0 0 auto;border-radius:50%;display:block}
    header.bar .wordmark{font-family:var(--serif);font-weight:700;font-size:16px;color:var(--ink)}
    header.bar .wordmark .stop{color:var(--accent)}
    header.bar .tag{font-size:11px;color:var(--ink-3);border-left:1px solid var(--line);padding-left:10px;margin-left:2px}
    header.bar .theme-toggle{
      margin-left:auto;width:32px;height:32px;flex:0 0 auto;border-radius:50%;
      border:1px solid var(--line);background:var(--surface);color:var(--ink-2);
      display:flex;align-items:center;justify-content:center;cursor:pointer;padding:0;
    }
    header.bar .theme-toggle:hover{border-color:var(--accent);color:var(--ink)}
    /* stroke set directly here rather than left to inherit currentColor
       from the button's `color` through button>svg>path — that chain is
       exactly where the icon was rendering blank. A direct stroke can't
       fail to resolve the same way. */
    header.bar .theme-toggle svg{width:16px;height:16px;stroke:var(--ink-2)}
    header.bar .theme-toggle:hover svg{stroke:var(--ink)}
    /* Which icon is showing is set directly via inline style by JS (see
       applyThemeIcon in THEME_INIT_SCRIPT/THEME_TOGGLE_SCRIPT), not by a
       CSS selector keyed off data-theme — a display:none default here was
       silently outliving the CSS override in some cascade, leaving the sun
       invisible in dark mode. JS setting style.display every time removes
       the ambiguity; these are just the pre-JS/no-JS fallback. */
    header.bar .theme-toggle .sun{display:none}
    .wrap{max-width:920px;margin:0 auto;padding:28px 20px 60px}
    @media (max-width:640px){
      header.bar .mark{width:28px;height:28px}
      .wrap{padding:0}
      main{padding:16px;border-radius:0;border-left:none;border-right:none;border-bottom:none}
      table{display:block;overflow-x:auto;white-space:nowrap;max-width:100%;-webkit-overflow-scrolling:touch}
    }
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
    nav.admin-nav{
      display:flex;flex-wrap:nowrap;gap:2px;margin:2px 0 22px;font-size:.92em;
      overflow-x:auto;-webkit-overflow-scrolling:touch;border-bottom:1px solid var(--line);
      scrollbar-width:none;
    }
    nav.admin-nav::-webkit-scrollbar{display:none}
    nav.admin-nav a,nav.admin-nav b{
      flex:0 0 auto;white-space:nowrap;padding:8px 14px;border-bottom:2px solid transparent;
      margin-bottom:-1px;
    }
    nav.admin-nav a{color:var(--ink-2)}
    nav.admin-nav a:hover{color:var(--ink);text-decoration:none}
    nav.admin-nav b{color:var(--ink);border-bottom-color:var(--accent);font-weight:600}
"""

# The app's own OIN mark (see app/static/home.html), inlined so the admin
# masthead reads as the same product without a fetched image.
#
# No clip-path: Safari has a long-standing bug where clip-path: url(#id)
# on an inline SVG sized purely by CSS (no width/height attributes, only
# viewBox) computes an empty clip region, silently dropping everything
# inside — which is exactly what a screenshot showed happening here (the
# navy background circle rendered, the cream lettering did not). The
# letter paths only poke ~1.6% past the circle's edge at their extreme
# tips (O's left curve, N's right stroke), invisible at the 28-32px this
# renders at, so dropping the clip changes nothing visible and removes
# the dependency entirely.
MARK_SVG = (
    "<svg class=mark viewBox='0 0 512 512' role=img aria-label='Open Indian News'>"
    "<circle cx=256 cy=256 r=248 fill='#1B3A66'/>"
    "<g fill='#F4F0E4'>"
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


# Runs before first paint so a stored preference (or the OS preference, on a
# first visit) applies without a flash of the wrong theme. Always writes the
# attribute explicitly rather than leaving the OS-preference case to the
# prefers-color-scheme media query alone, so the toggle button's icon swap
# (keyed off the data-theme attribute only) can't fall out of sync with it.
#
# The localStorage read is try/caught on its own, separately from the
# setAttribute call: Safari Private Browsing throws on localStorage access,
# and the two used to share one try block, so the throw was also skipping
# setAttribute — data-theme never got set, the page still looked dark via
# the plain prefers-color-scheme CSS, but the toggle button (which only
# checks the attribute) always believed it was in light mode and rendered
# the moon regardless of actual theme, hiding the sun.
THEME_INIT_SCRIPT = (
    "<script>(function(){"
    "var stored=null;try{stored=localStorage.getItem('oin_admin_theme');}catch(e){}"
    "var t=stored||(matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light');"
    "document.documentElement.setAttribute('data-theme',t);"
    "})()</script>"
)

# The moon/sun swap is done here in JS, not via a CSS selector keyed off
# data-theme: a CSS-only display:none/block pairing left the sun invisible
# in dark mode in practice, so this sets style.display on both icons
# directly, on load and on every click, removing any cascade ambiguity.
THEME_TOGGLE_SCRIPT = (
    "<script>(function(){"
    "var root=document.documentElement;"
    "var btn=document.getElementById('theme-toggle');"
    "var moon=btn.querySelector('.moon'),sun=btn.querySelector('.sun');"
    "function paint(){"
    "var dark=root.getAttribute('data-theme')==='dark';"
    "moon.style.display=dark?'none':'block';"
    "sun.style.display=dark?'block':'none';"
    "}"
    "paint();"
    "btn.addEventListener('click',function(){"
    "var next=root.getAttribute('data-theme')==='dark'?'light':'dark';"
    "root.setAttribute('data-theme',next);"
    "try{localStorage.setItem('oin_admin_theme',next);}catch(e){}"
    "paint();"
    "});"
    "})()</script>"
)

THEME_TOGGLE_BTN = (
    "<button id=theme-toggle class=theme-toggle type=button aria-label='Toggle dark mode'>"
    "<svg class=moon viewBox='0 0 24 24' fill=none stroke=currentColor stroke-width=2 "
    "stroke-linecap=round stroke-linejoin=round><path d='M21 12.79A9 9 0 1 1 11.21 3 "
    "7 7 0 0 0 21 12.79Z'/></svg>"
    "<svg class=sun viewBox='0 0 24 24' fill=none stroke=currentColor stroke-width=2 "
    "stroke-linecap=round stroke-linejoin=round><circle cx=12 cy=12 r=4/>"
    "<path d='M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2"
    "M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41'/></svg>"
    "</button>"
)


def layout(title: str, body: str) -> HTMLResponse:
    header = (
        "<header class=bar><a class=brand href='/'>"
        f"{MARK_SVG}<span class=wordmark>Open Indian News<span class=stop>.</span></span>"
        f"</a><span class=tag>Admin</span>{THEME_TOGGLE_BTN}</header>"
    )
    return HTMLResponse(
        f"<!doctype html><html><head><meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<meta name=color-scheme content='light dark'>"
        f"<meta name=theme-color content='#ffffff' media='(prefers-color-scheme: light)'>"
        f"<meta name=theme-color content='#121212' media='(prefers-color-scheme: dark)'>"
        f"<link rel=icon href=\"data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' "
        f"viewBox='0 0 32 32'><rect width='32' height='32' rx='6' fill='%23171717'/>"
        f"<text x='16' y='23' font-family='Georgia,serif' font-size='20' font-weight='700' "
        f"fill='%23fff' text-anchor='middle'>O</text></svg>\">"
        f"<title>{title}</title><style>{STYLE}</style>{THEME_INIT_SCRIPT}</head>"
        f"<body>{header}<div class=wrap><main>{body}</main></div>{THEME_TOGGLE_SCRIPT}</body></html>")


def login_form(title: str, action: str) -> HTMLResponse:
    return layout(title, (
        f"<h1>{title}</h1><form method=post action='{action}'>"
        "<label>Username<input name=username required></label>"
        "<label>Password<input name=password type=password required></label>"
        "<button>Sign in</button></form>"))
