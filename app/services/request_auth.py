"""
Per-request identity for the `/users/{user_id}/...` endpoints.

Before this existed, `/auth/login` verified a Firebase ID token and every
subsequent call simply trusted whatever `user_id` the client put in the path.
Internal ids (`usr_<hex>`) are not secret — they travel in URLs, logs and
crash reports — so anyone who learned or guessed one could read another
account's saved stories, reading history, game stats and starred/blocked
sources, and write to them. That is a data-protection failure, not merely a
hardening gap, which is why enforcement here is unconditional.

The contract: the caller sends `Authorization: Bearer <firebase id token>`.
The token is verified cryptographically, mapped to a User row via
`provider_uid`, and the resulting id must equal the `user_id` in the path.
The path id is therefore no longer a claim of identity — it is a claim the
server checks.

Verification itself is local (the Admin SDK caches Google's signing keys), but
it is still an RSA verify per request on a 1 vCPU droplet, so verified tokens
are memoised until shortly before they expire. The cache is keyed on a hash of
the token, never the token itself.
"""
from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.database import get_db
from app.models import User
from app.services.firebase_auth import (
    InvalidFirebaseIdToken,
    verify_firebase_id_token,
)

logger = logging.getLogger(__name__)

# Verified-token cache: token digest -> (internal user id, expiry epoch).
# Small and process-local; both droplets keep their own, which is fine because
# it holds no authority of its own — a cache miss just re-verifies.
_TOKEN_CACHE: dict[str, tuple[str, float]] = {}
_CACHE_TTL_SECONDS = 300
# Bounded so a flood of distinct tokens can't grow it without limit.
_CACHE_MAX_ENTRIES = 5000


@dataclass(frozen=True)
class CallerIdentity:
    """The authenticated caller. `user_id` is our internal id, not Firebase's."""
    user_id: str
    firebase_uid: str


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _cached_user_id(token: str) -> Optional[str]:
    entry = _TOKEN_CACHE.get(_digest(token))
    if entry is None:
        return None
    user_id, expires_at = entry
    if expires_at <= time.time():
        _TOKEN_CACHE.pop(_digest(token), None)
        return None
    return user_id


def _remember(token: str, user_id: str) -> None:
    if len(_TOKEN_CACHE) >= _CACHE_MAX_ENTRIES:
        # Cheapest sufficient eviction: drop everything already expired, and
        # if that frees nothing, clear the map. Re-verification is correct,
        # just slower, so a blunt reset is safe.
        now = time.time()
        for key in [k for k, (_, exp) in _TOKEN_CACHE.items() if exp <= now]:
            _TOKEN_CACHE.pop(key, None)
        if len(_TOKEN_CACHE) >= _CACHE_MAX_ENTRIES:
            _TOKEN_CACHE.clear()
    _TOKEN_CACHE[_digest(token)] = (user_id, time.time() + _CACHE_TTL_SECONDS)


def invalidate_token_cache_for_user(user_id: str) -> None:
    """
    Drop every cached token for a user. Called on account deletion so a
    still-unexpired token cannot act on a row that no longer exists.
    """
    for key in [k for k, (uid, _) in _TOKEN_CACHE.items() if uid == user_id]:
        _TOKEN_CACHE.pop(key, None)


def _bearer_token(authorization: Optional[str]) -> str:
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="Missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(
            status_code=401,
            detail="Authorization header must be 'Bearer <firebase id token>'",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token.strip()


async def _resolve(token: str, db: AsyncSession) -> str:
    """Verify a token and return the internal user id it maps to."""
    cached = _cached_user_id(token)
    if cached is not None:
        return cached

    try:
        identity = await verify_firebase_id_token(token)
    except InvalidFirebaseIdToken as e:
        raise HTTPException(
            status_code=401,
            detail=f"Invalid Firebase ID token: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        )

    result = await db.execute(select(User).where(User.provider_uid == identity.uid))
    user = result.scalar_one_or_none()
    if user is None:
        # A valid Firebase token with no local row: the account was deleted,
        # or the client never completed /auth/login. Either way there is
        # nothing to act on, and 401 tells the client to log in again.
        raise HTTPException(status_code=401, detail="No account for this token")

    _remember(token, user.id)
    return user.id


async def require_user(
    user_id: str,
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
) -> CallerIdentity:
    """
    Dependency for `/users/{user_id}/...`: proves the caller *is* `user_id`.

    Picks up `user_id` from the path automatically — FastAPI matches
    dependency parameters against the same request the endpoint sees, so no
    endpoint has to pass it in.

    403 rather than 404 on a mismatch: the caller is authenticated, just not
    authorised for this id. Deliberately does not reveal whether the id
    exists.
    """
    token = _bearer_token(authorization)
    caller_id = await _resolve(token, db)
    if caller_id != user_id:
        logger.warning(
            "Rejected cross-account request: caller %s addressed %s", caller_id, user_id
        )
        raise HTTPException(status_code=403, detail="Not your account")
    return CallerIdentity(user_id=caller_id, firebase_uid="")


async def optional_user_id(
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
) -> Optional[str]:
    """
    For endpoints that work anonymously but personalise when signed in
    (`/clusters`, `/clusters/for-you`). Returns the verified internal id, or
    None when there is no usable credential.

    A bad or expired token degrades to anonymous rather than failing the
    request: these endpoints' value is the feed itself, and a stale token
    should not turn the app's main screen into an error. Nothing here is
    written on behalf of the user except bandit exposures, which are
    worthless to forge.
    """
    if not authorization:
        return None
    try:
        return await _resolve(_bearer_token(authorization), db)
    except HTTPException:
        return None
