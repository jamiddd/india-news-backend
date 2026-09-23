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
import html
import re
import secrets
import time
from urllib.parse import parse_qs

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

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


def _publicize_admin_html(body: str) -> str:
    """Rewrite generated internal admin links/forms to public paths."""
    body = re.sub(r"([\"'])/admin(?=\1)", r"\1/", body)
    return re.sub(r"([\"'])/admin(?=[/?!])", r"\1", body)


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
      --bg:#ffffff; --surface:#F3F3F8; --content-bg:#ffffff; --line:#e4e4ea;
      --accent:#1976D2; --accent-tint:rgba(25,118,210,0.10); --on-accent:#FFFFFF;
      --danger:#C62828; --done:#2E7D32;
      --serif:"Source Serif 4",Georgia,"Times New Roman",serif;
      --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
      --radius:14px;
      --header-h:60px; --sidebar-w:220px;
    }
    @media (prefers-color-scheme:dark){ :root:not([data-theme=light]){
      --ink:#E8E8E8; --ink-2:#b6b6b6; --ink-3:#8d8d8d;
      --bg:#121212; --surface:#121212; --content-bg:#1E1E24; --line:#2c2c34;
      --accent:#64B5F6; --accent-tint:rgba(100,181,246,0.14); --on-accent:#171717;
      --danger:#EF5350; --done:#66BB6A;
    }}
    :root[data-theme=dark]{
      --ink:#E8E8E8; --ink-2:#b6b6b6; --ink-3:#8d8d8d;
      --bg:#121212; --surface:#121212; --content-bg:#1E1E24; --line:#2c2c34;
      --accent:#64B5F6; --accent-tint:rgba(100,181,246,0.14); --on-accent:#171717;
      --danger:#EF5350; --done:#66BB6A;
    }
    *{box-sizing:border-box}
    body{margin:0;background:var(--surface);color:var(--ink);font-family:var(--sans);line-height:1.55}
    h1,h2{font-family:var(--serif);font-weight:700;letter-spacing:-0.01em;margin:0}
    h1{font-size:1.5rem}
    h2{font-size:1.05rem;margin:0 0 4px}
    a{color:var(--accent);text-decoration:none}
    a:hover{text-decoration:underline}
    header.bar{
      height:var(--header-h);flex:0 0 auto;position:sticky;top:0;z-index:20;
      background:var(--bg);border-bottom:1px solid var(--line);
      padding:0 20px;display:flex;align-items:center;gap:12px;
    }
    header.bar .hamburger{
      display:none;width:32px;height:32px;flex:0 0 auto;border-radius:8px;
      border:1px solid var(--line);background:var(--surface);
      align-items:center;justify-content:center;cursor:pointer;padding:0;
    }
    header.bar .hamburger span,header.bar .hamburger span::before,header.bar .hamburger span::after{
      content:"";display:block;width:16px;height:2px;background:var(--ink-2);border-radius:2px;position:relative;
    }
    header.bar .hamburger span::before{position:absolute;top:-5px}
    header.bar .hamburger span::after{position:absolute;top:5px}
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
    header.bar .theme-toggle span{font-size:15px;line-height:1;color:inherit}
    /* Which icon is showing is set directly via inline style by JS (see
       THEME_TOGGLE_SCRIPT's paint()), not by a CSS selector keyed off
       data-theme, so it can't fall out of sync with the actual state.
       This is just the pre-JS/no-JS fallback. */
    header.bar .theme-toggle .sun{display:none}

    /* Shell: toolbar above, sidebar + scrollable content below. The sidebar
       lives in normal flex flow (not position:fixed/absolute) so that on
       narrow screens opening it can push the content sideways instead of
       overlaying it — see the mobile block below. */
    html,body{height:100%;overflow:hidden}
    .shell{display:flex;flex-direction:column;height:100vh}
    .shell-body{display:flex;flex:1;min-height:0}
    nav.sidebar{
      flex:0 0 var(--sidebar-w);width:var(--sidebar-w);overflow-y:auto;
      background:var(--bg);border-right:1px solid var(--line);padding:14px 10px;
    }
    nav.sidebar .group-label{
      font-size:.72em;color:var(--ink-3);text-transform:uppercase;letter-spacing:.06em;
      padding:10px 10px 6px;
    }
    nav.sidebar a{
      display:flex;align-items:center;gap:9px;padding:8px 10px;border-radius:8px;
      color:var(--ink-2);font-size:.93em;margin-bottom:1px;
    }
    nav.sidebar a:hover{background:var(--surface);color:var(--ink);text-decoration:none}
    nav.sidebar a.current{background:var(--accent-tint);color:var(--accent);font-weight:600}
    .wrap{flex:1;min-width:0;overflow-y:auto;overflow-x:hidden}
    .wrap-inner{max-width:900px;margin:0 auto;padding:28px 24px 60px}
    @media (max-width:899px){
      header.bar .hamburger{display:flex}
      .shell-body{position:relative;overflow-x:hidden}
      nav.sidebar{
        flex-basis:0;width:0;padding-left:0;padding-right:0;border-right-width:0;
        overflow:hidden;white-space:nowrap;transition:flex-basis .22s ease,width .22s ease,padding .22s ease;
      }
      .shell-body.nav-open nav.sidebar{
        flex-basis:calc(var(--sidebar-w) - 40px);width:calc(var(--sidebar-w) - 40px);
        padding:14px 10px;border-right-width:1px;
      }
      .wrap{flex:0 0 100%}
    }
    @media (max-width:640px){
      header.bar .mark{width:28px;height:28px}
      table{display:block;overflow-x:auto;white-space:nowrap;max-width:100%;-webkit-overflow-scrolling:touch}
    }
    main{min-height:calc(100% - 1px);padding:28px;background:var(--content-bg);border:1px solid var(--line);border-radius:var(--radius);box-shadow:0 2px 8px rgba(23,23,23,.06)}
    @media (max-width:640px){
      .wrap-inner{padding:0 0 40px}
      main{padding:20px 16px;border-radius:0}
    }
    h1,h2{font-family:var(--sans)}
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
    .admin-tabs{display:flex;gap:4px;border-bottom:1px solid var(--line);margin:22px 0 20px}
    .admin-tab-form{display:inline;margin:0}
    .admin-tabs button.admin-tab{cursor:pointer;padding:9px 14px;margin:0 0 -1px;border:1px solid transparent;border-radius:8px 8px 0 0;background:transparent;color:var(--ink-2)}
    .admin-tabs button.admin-tab:hover{background:var(--bg);border-color:var(--line)}
    .admin-tabs button.admin-tab.current{border-color:var(--line);border-bottom-color:var(--bg);background:var(--bg);color:var(--accent);font-weight:600}
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
    button:disabled{opacity:.5;cursor:not-allowed}
    button:disabled:hover{border-color:var(--line)}
    button.busy{opacity:1;cursor:progress;border-color:var(--accent);color:var(--accent)}
    button.busy::before{
      content:"";display:inline-block;width:.9em;height:.9em;margin-right:8px;vertical-align:-.12em;
      border:2px solid currentColor;border-right-color:transparent;border-radius:50%;
      animation:busy-spin .7s linear infinite;
    }
    @keyframes busy-spin{to{transform:rotate(360deg)}}
    @media (prefers-reduced-motion:reduce){button.busy::before{animation-duration:2s}}
    .busy-note{
      position:fixed;left:50%;bottom:24px;transform:translateX(-50%);z-index:50;
      max-width:min(92vw,460px);padding:10px 16px;border:1px solid var(--line);border-radius:10px;
      background:var(--surface);color:var(--ink);box-shadow:0 6px 24px rgba(0,0,0,.18);
      font-size:.94em;text-align:center;
    }

    /* ---------- custom dropdown (see custom_select()) ----------
       Replaces the browser's native <select> chrome so the trigger and the
       open panel both pick up the same tokens as everything else in the
       form (border, radius, focus ring, dark mode) instead of the OS
       widget. Only the trigger button is a real focusable control; the
       panel is a tabindex=-1 listbox that DROPDOWN_SCRIPT opens/closes and
       drives with arrow keys, matching the native <select> keyboard model. */
    .dd{position:relative;width:100%;margin:5px 0 12px}
    .dd-btn{
      width:100%;display:flex;align-items:center;justify-content:space-between;gap:10px;
      padding:10px 12px;margin:0;border:1px solid var(--line);border-radius:8px;
      background:var(--bg);color:var(--ink);font:inherit;text-align:left;cursor:pointer;
    }
    .dd-btn:hover{border-color:var(--accent)}
    .dd-btn:focus-visible{outline:2px solid var(--accent);outline-offset:1px;border-color:transparent}
    .dd-label{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    .dd-chevron{width:16px;height:16px;flex:0 0 auto;color:var(--ink-3);transition:transform .15s ease}
    .dd.is-open .dd-chevron{transform:rotate(180deg)}
    .dd-panel{
      position:absolute;left:0;right:0;top:calc(100% + 4px);z-index:30;
      margin:0;padding:6px;list-style:none;max-height:260px;overflow-y:auto;
      background:var(--content-bg);border:1px solid var(--line);border-radius:10px;
      box-shadow:0 8px 24px rgba(0,0,0,.16);
    }
    .dd-panel:focus{outline:none}
    .dd-opt{
      padding:9px 10px;border-radius:6px;font-size:.94em;color:var(--ink);cursor:pointer;
      white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
    }
    .dd-opt:hover,.dd-opt.is-active{background:var(--surface)}
    .dd-opt.is-selected{color:var(--accent);font-weight:600}
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

# Grouped for the sidebar: "Review" is the daily in-and-out (drafts that
# expire if nobody acts today), "Manage" is everything else, looked at less
# often. Matches pending_reviews()/admin_notify.py's notion of what's urgent.
NAV_GROUPS = [
    ("Review", [
        ("/admin", "Daily review"),
        ("/admin/polls", "Polls"),
        ("/admin/quiz", "Quiz"),
        ("/admin/quiz-bank", "Quiz bank"),
        ("/admin/poll-bank", "Poll bank"),
        ("/admin/feedback", "Feedback"),
        ("/admin/reports", "Story reports"),
    ]),
    ("Manage", [
        ("/admin/donations", "Donations"),
        ("/admin/users", "Users"),
        ("/admin/timelines", "Timelines"),
        ("/admin/breaking", "Breaking review"),
        ("/admin/topics", "Topics"),
        ("/admin/announcements", "Announcements"),
    ]),
]


def custom_select(name: str, options: list[tuple[str, str]], selected: str | None = None) -> str:
    """Renders a `.dd` custom dropdown (see STYLE and DROPDOWN_SCRIPT) that
    posts like a native <select name=NAME>: a hidden input carries the
    value, a styled button shows the current label, and a listbox panel
    holds the options. `options` is [(value, label), ...]; with no match
    for `selected` (or none given) the first option is picked, same as a
    plain <select> with no `selected` attribute set."""
    values = [v for v, _ in options]
    if selected not in values:
        selected = values[0] if values else ""
    selected_label = next((lbl for v, lbl in options if v == selected), "")
    dd_id = f"dd-{name}"
    opts_html = "".join(
        f"<li role=option id='{dd_id}-opt-{i}' data-value='{html.escape(v)}' "
        f"class='dd-opt{' is-selected' if v == selected else ''}' "
        f"aria-selected='{'true' if v == selected else 'false'}'>{html.escape(lbl)}</li>"
        for i, (v, lbl) in enumerate(options)
    )
    return (
        f"<div class=dd id='{dd_id}'>"
        "<button type=button class=dd-btn aria-haspopup=listbox aria-expanded=false>"
        f"<span class=dd-label>{html.escape(selected_label)}</span>"
        "<svg class=dd-chevron viewBox='0 0 20 20' aria-hidden=true focusable=false>"
        "<path d='M5.5 7.5 10 12l4.5-4.5' fill=none stroke=currentColor stroke-width=1.6 "
        "stroke-linecap=round stroke-linejoin=round/></svg>"
        "</button>"
        f"<ul class=dd-panel role=listbox tabindex=-1 hidden>{opts_html}</ul>"
        f"<input type=hidden name='{html.escape(name)}' value='{html.escape(selected)}'>"
        "</div>"
    )


# Drives every `.dd` custom dropdown (see custom_select()/STYLE). The
# trigger button toggles the panel; the panel itself takes focus (tabindex
# -1) while open so arrow keys/Home/End/Enter/Escape work on it directly,
# same keys a native <select> responds to. Selecting an option writes the
# hidden input's value and fires a `change` event on it, so any future code
# that listens for `change` sees the same event a native <select> would emit.
DROPDOWN_SCRIPT = """<script>(function(){
function opts(dd){return Array.prototype.slice.call(dd.querySelectorAll('.dd-opt'));}
function setActive(dd,list,index){
  list.forEach(function(o,i){o.classList.toggle('is-active',i===index);});
  var panel=dd.querySelector('.dd-panel');
  if(list[index]){panel.setAttribute('aria-activedescendant',list[index].id);list[index].scrollIntoView({block:'nearest'});}
}
function closeDd(dd,refocus){
  var btn=dd.querySelector('.dd-btn'),panel=dd.querySelector('.dd-panel');
  dd.classList.remove('is-open');btn.setAttribute('aria-expanded','false');panel.hidden=true;
  if(refocus)btn.focus();
}
function openDd(dd){
  document.querySelectorAll('.dd.is-open').forEach(function(o){if(o!==dd)closeDd(o,false);});
  var btn=dd.querySelector('.dd-btn'),panel=dd.querySelector('.dd-panel'),list=opts(dd);
  dd.classList.add('is-open');btn.setAttribute('aria-expanded','true');panel.hidden=false;
  var idx=list.findIndex(function(o){return o.classList.contains('is-selected');});
  setActive(dd,list,idx<0?0:idx);
  panel.focus();
}
function selectOpt(dd,opt){
  closeDd(dd,true);
  opts(dd).forEach(function(o){o.classList.remove('is-selected');o.setAttribute('aria-selected','false');});
  opt.classList.add('is-selected');opt.setAttribute('aria-selected','true');
  dd.querySelector('.dd-label').textContent=opt.textContent;
  var input=dd.querySelector('input[type=hidden]');
  input.value=opt.getAttribute('data-value');
  input.dispatchEvent(new Event('change',{bubbles:true}));
}
document.querySelectorAll('.dd').forEach(function(dd){
  var btn=dd.querySelector('.dd-btn'),panel=dd.querySelector('.dd-panel');
  btn.addEventListener('click',function(e){
    e.stopPropagation();
    dd.classList.contains('is-open')?closeDd(dd,false):openDd(dd);
  });
  btn.addEventListener('keydown',function(e){
    if(['ArrowDown','ArrowUp','Enter',' '].indexOf(e.key)===-1)return;
    e.preventDefault();
    if(!dd.classList.contains('is-open'))openDd(dd);
  });
  // mousedown, not click: Safari can skip firing `click` on a plain
  // <li> when the preceding mousedown blurs the focused listbox (which
  // is exactly what selecting an option does here), so the panel's
  // hide-on-select never ran in Safari even though Chrome/Firefox fired
  // click reliably. mousedown always fires, and preventDefault stops the
  // native focus/selection side effects that caused the Safari gap.
  panel.addEventListener('mousedown',function(e){
    var opt=e.target.closest('.dd-opt');
    if(!opt)return;
    e.preventDefault();
    selectOpt(dd,opt);
  });
  panel.addEventListener('mouseover',function(e){
    var opt=e.target.closest('.dd-opt');
    if(!opt)return;
    setActive(dd,opts(dd),opts(dd).indexOf(opt));
  });
  panel.addEventListener('keydown',function(e){
    var list=opts(dd);
    var idx=list.findIndex(function(o){return o.classList.contains('is-active');});
    if(idx<0)idx=0;
    if(e.key==='ArrowDown'){e.preventDefault();setActive(dd,list,Math.min(idx+1,list.length-1));}
    else if(e.key==='ArrowUp'){e.preventDefault();setActive(dd,list,Math.max(idx-1,0));}
    else if(e.key==='Home'){e.preventDefault();setActive(dd,list,0);}
    else if(e.key==='End'){e.preventDefault();setActive(dd,list,list.length-1);}
    else if(e.key==='Enter'||e.key===' '){e.preventDefault();if(list[idx])selectOpt(dd,list[idx]);}
    else if(e.key==='Escape'){e.preventDefault();closeDd(dd,true);}
    else if(e.key==='Tab'){closeDd(dd,false);}
  });
});
document.addEventListener('click',function(e){
  document.querySelectorAll('.dd.is-open').forEach(function(dd){
    if(!dd.contains(e.target))closeDd(dd,false);
  });
});
})()</script>"""


def _sidebar(current: str | None) -> str:
    groups = "".join(
        f"<div class=group-label>{group}</div>" + "".join(
            f"<a class=current href='{admin_public_path(href)}'>{label}</a>" if href == current
            else f"<a href='{admin_public_path(href)}'>{label}</a>"
            for href, label in links)
        for group, links in NAV_GROUPS)
    return f"<nav class=sidebar>{groups}</nav>"


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

# Plain glyphs, not inline SVG: this exact spot has now hit two separate
# real SVG rendering bugs in Safari (the mark's clip-path clipping its
# content to nothing, and this button's sun path painting nothing despite
# every toggle/color mechanism around it provably working). Text picks up
# `color` directly with none of the fill/stroke/inheritance machinery, so
# it sidesteps the whole bug class rather than chasing a third cause.
THEME_TOGGLE_BTN = (
    "<button id=theme-toggle class=theme-toggle type=button aria-label='Toggle dark mode'>"
    "<span class=moon aria-hidden=true>☾</span>"
    "<span class=sun aria-hidden=true>☀</span>"
    "</button>"
)

HAMBURGER_BTN = (
    "<button id=nav-toggle class=hamburger type=button aria-label='Toggle navigation'>"
    "<span></span></button>"
)

# Toggles a class on .shell-body rather than the sidebar itself: the sidebar's
# width/flex-basis transition (see STYLE's @media (max-width:899px) block) is
# what makes it push the content over instead of overlaying it, and that
# transition is keyed off the ancestor class so one toggle affects both the
# sidebar and, if it ever needs to react too, the content pane next to it.
NAV_TOGGLE_SCRIPT = (
    "<script>(function(){"
    "var btn=document.getElementById('nav-toggle');"
    "var shellBody=document.querySelector('.shell-body');"
    "if(!btn||!shellBody)return;"
    "btn.addEventListener('click',function(){shellBody.classList.toggle('nav-open');});"
    "})()</script>"
)


# Every admin action is a plain POST form whose handler blocks until Claude (or
# the DB) answers, which can take a while — with no feedback the page looks
# dead and the operator clicks again. On submit this locks *every* POST form's
# buttons on the page (not just the clicked form: approving one breaking
# candidate while regenerating another would race), spins the clicked button,
# and shows a status note. A form with data-busy-msg overrides the note text.
#
# The clicked button's name/value is copied into a hidden input *before*
# anything is disabled: disabled controls are left out of the form data, so
# without this `action=approve|regenerate|reject` would silently vanish.
# A second submit while locked is cancelled, and `pageshow` unlocks the page
# when the browser restores it from the back/forward cache still disabled.
# Forms whose own onsubmit confirm() was declined arrive with defaultPrevented
# set and are left alone.
BUSY_SUBMIT_SCRIPT = """<script>(function(){
var locked=false,note=null;
function unlock(){
  locked=false;
  if(note){note.remove();note=null;}
  document.querySelectorAll('button.busy').forEach(function(b){
    b.classList.remove('busy');if(b.dataset.idleLabel!==undefined){b.textContent=b.dataset.idleLabel;}
  });
  document.querySelectorAll('button[data-was-enabled]').forEach(function(b){
    b.disabled=false;b.removeAttribute('data-was-enabled');
  });
  document.querySelectorAll('input[data-busy-carry]').forEach(function(i){i.remove();});
}
document.addEventListener('submit',function(e){
  var form=e.target;
  if(e.defaultPrevented||!form||(form.method||'').toLowerCase()!=='post')return;
  if(locked){e.preventDefault();return;}
  locked=true;
  var sub=e.submitter||form.querySelector('button:not([type=button]),input[type=submit]');
  if(sub&&sub.name){
    var carry=document.createElement('input');
    carry.type='hidden';carry.name=sub.name;carry.value=sub.value;
    carry.setAttribute('data-busy-carry','');
    form.appendChild(carry);
  }
  document.querySelectorAll('button:not([type=button]),input[type=submit]').forEach(function(b){
    if(!b.disabled){b.setAttribute('data-was-enabled','');b.disabled=true;}
  });
  if(sub){
    sub.classList.add('busy');
    if(sub.tagName==='BUTTON'){sub.dataset.idleLabel=sub.textContent;}
  }
  note=document.createElement('div');
  note.className='busy-note';note.setAttribute('role','status');
  note.textContent=form.getAttribute('data-busy-msg')||
    'Working\\u2026 this can take up to a minute. Please don\\u2019t reload or click again.';
  document.body.appendChild(note);
});
window.addEventListener('pageshow',function(e){if(e.persisted)unlock();});
})()</script>"""


def layout(title: str, body: str, current: str | None = None) -> HTMLResponse:
    """Renders the admin shell. `current` is the href of the page rendering
    it — pass it to get the sidebar with that page highlighted; omit it (as
    the login page does) to render without a sidebar at all."""
    header = (
        "<header class=bar>"
        + (HAMBURGER_BTN if current else "")
        + "<a class=brand href='/'>"
        f"{MARK_SVG}<span class=wordmark>Open Indian News<span class=stop>.</span></span>"
        f"</a><span class=tag>Admin</span>{THEME_TOGGLE_BTN}</header>"
    )
    sidebar = _sidebar(current) if current else ""
    body = _publicize_admin_html(body)
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
        f"<body><div class=shell>{header}<div class=shell-body>{sidebar}"
        f"<div class=wrap><div class=wrap-inner><main>{body}</main></div></div>"
        f"</div></div>{THEME_TOGGLE_SCRIPT}{NAV_TOGGLE_SCRIPT if current else ''}"
        f"{DROPDOWN_SCRIPT}{BUSY_SUBMIT_SCRIPT}</body></html>")


def login_form(title: str, action: str) -> HTMLResponse:
    return layout(title, (
        f"<h1>{title}</h1><form method=post action='{action}'>"
        "<label>Username<input name=username required></label>"
        "<label>Password<input name=password type=password required></label>"
        "<button>Sign in</button></form>"))
