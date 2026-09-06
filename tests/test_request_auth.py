"""Covers the per-request identity check for /users/{user_id} endpoints.

The property under test is the one that matters: a caller can only act on the
account their token maps to. Everything else here (header parsing, the
verified-token cache) exists to support that, so it is tested for the ways it
could weaken the check — never expiring, growing without bound, or surviving
account deletion.
"""
from __future__ import annotations

import time

import pytest
from fastapi import HTTPException

from app.services import request_auth
from app.services.request_auth import (
    _bearer_token,
    _cached_user_id,
    _remember,
    invalidate_token_cache_for_user,
)


@pytest.fixture(autouse=True)
def clear_cache():
    request_auth._TOKEN_CACHE.clear()
    yield
    request_auth._TOKEN_CACHE.clear()


class TestBearerParsing:
    def test_extracts_the_token(self):
        assert _bearer_token("Bearer abc.def.ghi") == "abc.def.ghi"

    def test_scheme_is_case_insensitive(self):
        assert _bearer_token("bearer abc") == "abc"

    @pytest.mark.parametrize(
        "header",
        [None, "", "abc", "Basic abc", "Bearer", "Bearer   "],
    )
    def test_rejects_anything_else_with_401(self, header):
        with pytest.raises(HTTPException) as exc:
            _bearer_token(header)
        assert exc.value.status_code == 401


class TestTokenCache:
    def test_round_trips_a_verified_token(self):
        _remember("tok", "usr_abc")
        assert _cached_user_id("tok") == "usr_abc"

    def test_unknown_token_is_a_miss(self):
        assert _cached_user_id("never-seen") is None

    def test_never_stores_the_raw_token(self):
        _remember("secret-token", "usr_abc")
        assert "secret-token" not in request_auth._TOKEN_CACHE

    def test_expired_entry_is_a_miss(self, monkeypatch):
        monkeypatch.setattr(request_auth, "_CACHE_TTL_SECONDS", -1)
        _remember("tok", "usr_abc")
        assert _cached_user_id("tok") is None

    def test_entry_expires_on_its_own_clock(self):
        digest = request_auth._digest("tok")
        request_auth._TOKEN_CACHE[digest] = ("usr_abc", time.time() - 1)
        assert _cached_user_id("tok") is None

    def test_stays_bounded(self, monkeypatch):
        monkeypatch.setattr(request_auth, "_CACHE_MAX_ENTRIES", 10)
        for i in range(50):
            _remember(f"tok{i}", f"usr_{i}")
        assert len(request_auth._TOKEN_CACHE) <= 10

    def test_deleting_an_account_drops_its_tokens(self):
        _remember("tok-a", "usr_gone")
        _remember("tok-b", "usr_gone")
        _remember("tok-c", "usr_stays")

        invalidate_token_cache_for_user("usr_gone")

        assert _cached_user_id("tok-a") is None
        assert _cached_user_id("tok-b") is None
        assert _cached_user_id("tok-c") == "usr_stays"
