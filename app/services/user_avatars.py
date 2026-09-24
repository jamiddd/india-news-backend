"""Profile pictures, stored in a public Supabase Storage bucket.

The app uploads the bytes to the backend (never straight to Storage), so the
service key stays server-side and the backend can enforce size/type limits.
Objects are named `{user_id}/{sha256[:16]}.{ext}`: a new photo is a new url,
which is what lets clients cache avatar urls forever.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Optional

import httpx

from app.config import settings
from app.services.editorial_backgrounds import project_base_url

logger = logging.getLogger(__name__)

MAX_AVATAR_BYTES = 2 * 1024 * 1024
_EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


def extension_for(content_type: str) -> Optional[str]:
    return _EXTENSIONS.get((content_type or "").split(";")[0].strip().lower())


def _public_prefix() -> str:
    base = project_base_url()
    return f"{base}/storage/v1/object/public/{settings.USER_AVATAR_BUCKET}/" if base else ""


def _headers(content_type: Optional[str] = None) -> dict:
    headers = {
        "Authorization": f"Bearer {settings.SUPABASE_SERVICE_KEY}",
        "apikey": settings.SUPABASE_SERVICE_KEY or "",
    }
    if content_type:
        headers["Content-Type"] = content_type
        headers["x-upsert"] = "true"
    return headers


def configured() -> bool:
    return bool(settings.SUPABASE_URL and settings.SUPABASE_SERVICE_KEY)


async def upload_avatar(user_id: str, data: bytes, content_type: str) -> Optional[str]:
    """Store one avatar and return its public url, or None on failure."""
    ext = extension_for(content_type)
    if not configured() or ext is None:
        return None
    name = f"{user_id}/{hashlib.sha256(data).hexdigest()[:16]}.{ext}"
    base = project_base_url()
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.put(
                f"{base}/storage/v1/object/{settings.USER_AVATAR_BUCKET}/{name}",
                headers=_headers(content_type),
                content=data,
            )
            response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Avatar upload for %s failed: %s: %s", user_id, type(exc).__name__, exc)
        return None
    return f"{_public_prefix()}{name}"


async def delete_avatar(url: Optional[str]) -> None:
    """Best-effort removal of a previously uploaded avatar. Urls that don't
    live in our bucket (e.g. a Google-provided photo) are ignored."""
    prefix = _public_prefix()
    if not url or not prefix or not url.startswith(prefix) or not configured():
        return
    name = url[len(prefix):]
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.delete(
                f"{project_base_url()}/storage/v1/object/{settings.USER_AVATAR_BUCKET}/{name}",
                headers=_headers(),
            )
            response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Avatar delete of %s failed: %s: %s", name, type(exc).__name__, exc)
