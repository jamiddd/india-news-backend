"""Serves the public web app (the React+Vite SPA in web/).

Build output lives in web/dist (Vite base "/app-assets/", so index.html points
at /app-assets/assets/*.js|css). The Dockerfile does `COPY . /app/`, so in the
container that is /app/web/dist; web/dist must exist in the build context
(build it before `docker build`). Override with the WEB_DIST_DIR env var.

Three jobs:
  1. assets: web/dist/assets is mounted at /app-assets/assets (see main.py),
     long-cached because Vite fingerprints the filenames.
  2. render_shell(): the server-rendered share pages (/story/{id},
     /timelines/{id}, /explainers/{id}, and the static pages) keep injecting
     their own <title>/Open Graph tags, but into the SPA's index.html so a
     browser boots the app while a crawler still reads the right meta. They
     fall back to their old HTML when dist is missing (render_shell -> None).
  3. a catch-all GET fallback so SPA-only paths (/saved, /settings, /source/1
     ...) get the shell on a hard refresh. Anything the server owns is excluded.

Include `router` LAST in app/main.py (like admin_spa): the catch-all must come
after every registered route.
"""
from __future__ import annotations

import os
from html import escape
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

REPO_ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = Path(os.environ.get("WEB_DIST_DIR") or REPO_ROOT / "web" / "dist")

SITE_NAME = "Open Indian News"
SITE_URL = "https://openindiannews.com"

# First path segments the catch-all must never answer with the shell: server
# routes, the JSON API, other static mounts and files.
RESERVED_PREFIXES = (
    "api", "admin", "health", "status", "static", ".well-known",
    "download/apk", "app-assets", "docs", "redoc", "openapi.json",
)

router = APIRouter()


class SpaAssets(StaticFiles):
    """Vite fingerprints asset filenames, so they can be cached forever."""

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


def make_assets(dist: Path = DIST_DIR) -> Optional[SpaAssets]:
    """The /app-assets/assets mount, or None when the SPA isn't built."""
    directory = dist / "assets"
    return SpaAssets(directory=str(directory)) if directory.is_dir() else None


_cache: dict = {}


def _template(dist: Optional[Path] = None) -> Optional[str]:
    """index.html, re-read only when the file changes (a redeploy swaps it)."""
    index = (dist or DIST_DIR) / "index.html"
    try:
        mtime = index.stat().st_mtime_ns
    except OSError:
        return None
    hit = _cache.get(index)
    if hit and hit[0] == mtime:
        return hit[1]
    text = index.read_text()
    _cache[index] = (mtime, text)
    return text


def available() -> bool:
    return _template() is not None


def inject_meta(
    html: str,
    *,
    title: str,
    description: Optional[str] = None,
    url: Optional[str] = None,
    image: Optional[str] = None,
    og_type: str = "website",
) -> str:
    """Sets <title> and adds description/OG/Twitter tags before </head>.
    Takes RAW text and escapes it here."""
    full_title = escape(title if SITE_NAME in title else f"{title} — {SITE_NAME}")
    tags = [f'<meta property="og:title" content="{full_title}">',
            f'<meta property="og:type" content="{escape(og_type)}">',
            f'<meta property="og:site_name" content="{SITE_NAME}">']
    if description:
        d = escape(description)
        tags += [f'<meta name="description" content="{d}">',
                 f'<meta property="og:description" content="{d}">']
    if url:
        tags.append(f'<meta property="og:url" content="{escape(url)}">')
        tags.append(f'<link rel="canonical" href="{escape(url)}">')
    if image:
        tags.append(f'<meta property="og:image" content="{escape(image)}">')
        tags.append('<meta name="twitter:card" content="summary_large_image">')
    block = "\n".join(tags) + "\n"

    title_tag = f"<title>{full_title}</title>"
    lower = html.lower()
    start = lower.find("<title>")
    end = lower.find("</title>")
    if start != -1 and end > start:
        html = html[:start] + title_tag + html[end + len("</title>"):]
        lower = html.lower()
    else:
        block = title_tag + "\n" + block
    head_end = lower.find("</head>")
    if head_end == -1:
        return block + html
    return html[:head_end] + block + html[head_end:]


def render_shell(status_code: int = 200, **meta) -> Optional[HTMLResponse]:
    """SPA index.html with page meta injected, or None if dist isn't built
    (callers then fall back to their legacy HTML)."""
    template = _template()
    if template is None:
        return None
    html = inject_meta(template, **meta) if meta.get("title") else template
    return HTMLResponse(html, status_code=status_code,
                        headers={"Cache-Control": "no-cache"})


def is_reserved(path: str) -> bool:
    path = path.strip("/")
    return any(path == p or path.startswith(p + "/") for p in RESERVED_PREFIXES)


@router.get("/{path:path}", response_class=HTMLResponse, include_in_schema=False)
async def spa_fallback(path: str):
    # Unknown paths under server prefixes, and anything that looks like a file
    # (favicon.ico, robots.txt, source maps), stay real 404s.
    if is_reserved(path) or "." in path.rsplit("/", 1)[-1]:
        raise HTTPException(status_code=404, detail="Not found")
    response = render_shell()
    if response is None:
        raise HTTPException(status_code=404, detail="Not found")
    return response
