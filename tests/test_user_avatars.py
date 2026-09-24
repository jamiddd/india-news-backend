from __future__ import annotations

import pytest

from app.config import settings
from app.services import user_avatars as av


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(settings, "SUPABASE_URL", "https://proj.supabase.co/rest/v1", raising=False)
    monkeypatch.setattr(settings, "SUPABASE_SERVICE_KEY", "service-key", raising=False)
    monkeypatch.setattr(settings, "USER_AVATAR_BUCKET", "user-avatars", raising=False)


def test_extension_for_accepts_only_images():
    assert av.extension_for("image/jpeg") == "jpg"
    assert av.extension_for("image/PNG; charset=binary") == "png"
    assert av.extension_for("image/webp") == "webp"
    assert av.extension_for("image/gif") is None
    assert av.extension_for("") is None


@pytest.mark.asyncio
async def test_upload_rejects_unsupported_type_without_network():
    assert await av.upload_avatar("usr_1", b"x", "image/gif") is None


@pytest.mark.asyncio
async def test_delete_ignores_foreign_urls(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("must not hit the network for a non-bucket url")

    monkeypatch.setattr(av.httpx, "AsyncClient", boom)
    await av.delete_avatar("https://lh3.googleusercontent.com/a/photo")
    await av.delete_avatar(None)
