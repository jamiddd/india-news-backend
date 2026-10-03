"""Serves the admin single-page app (app/static/admin/).

Every GET under /admin that isn't the JSON API or an asset returns the same
shell, and the SPA's router picks the section from the path. That keeps every
URL the old server-rendered admin used working unchanged: /admin/polls from
the morning push (app/services/admin_notify.py), /admin/explainers/12/review
from a bookmark, and so on.

On admin.openindiannews.com Caddy rewrites /x to /admin/x (infra/Caddyfile),
so the browser sees paths without the /admin prefix while this app sees them
with it. The shell is told which base to use so its links, asset URLs and
API calls match what the browser is actually on.

Include this router LAST in app/main.py: its catch-all would otherwise
swallow any /admin route registered after it (e.g. /admin/engagement).
"""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

ADMIN_STATIC = Path(__file__).parent / "static" / "admin"
ADMIN_HOST = "admin.openindiannews.com"

router = APIRouter()


class AdminAssets(StaticFiles):
    """Asset URLs carry ?v=<content hash> (see _version), so a deploy busts
    the cache and anything unchanged stays cached."""

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


assets = AdminAssets(directory=str(ADMIN_STATIC))


@lru_cache(maxsize=1)
def _version() -> str:
    digest = hashlib.sha256()
    for path in sorted(ADMIN_STATIC.rglob("*")):
        if path.is_file() and path.name != "index.html":
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:10]


@lru_cache(maxsize=1)
def _template() -> str:
    return (ADMIN_STATIC / "index.html").read_text()


def _base(request: Request) -> str:
    return "" if (request.url.hostname or "").lower() == ADMIN_HOST else "/admin"


def _shell(request: Request) -> HTMLResponse:
    html = _template().replace("__BASE__", _base(request)).replace("__V__", _version())
    return HTMLResponse(html, headers={
        "Cache-Control": "no-store",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "same-origin",
    })


@router.get("/admin", response_class=HTMLResponse, include_in_schema=False)
async def spa_root(request: Request):
    return _shell(request)


@router.get("/admin/{path:path}", response_class=HTMLResponse, include_in_schema=False)
async def spa_path(path: str, request: Request):
    # An unknown /admin/api/... GET is a client bug, not a page: answer it
    # as JSON so the SPA's error handling sees a 404 instead of HTML.
    if path == "api" or path.startswith("api/") or path.startswith("assets/"):
        raise HTTPException(status_code=404, detail="Not found")
    return _shell(request)
