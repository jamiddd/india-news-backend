import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

import firebase_admin
from firebase_admin import auth as firebase_auth_sdk
from firebase_admin import credentials

from app.config import settings

logger = logging.getLogger(__name__)

_firebase_app: Optional[firebase_admin.App] = None


def _get_firebase_app() -> firebase_admin.App:
    """
    Lazily initialize the Firebase Admin SDK on first use rather than at
    import time, so a missing/misconfigured credential doesn't crash module
    import for code paths that don't need it yet.
    """
    global _firebase_app
    if _firebase_app is None:
        if not settings.FIREBASE_CREDENTIALS_PATH:
            raise RuntimeError("FIREBASE_CREDENTIALS_PATH not configured")
        cred = credentials.Certificate(settings.FIREBASE_CREDENTIALS_PATH)
        _firebase_app = firebase_admin.initialize_app(cred)
    return _firebase_app


@dataclass
class VerifiedFirebaseIdentity:
    uid: str  # Firebase's stable per-user id — same regardless of which
              # linked provider (password/Google) signed in
    email: str
    name: str
    email_verified: bool


class InvalidFirebaseIdToken(Exception):
    pass


async def verify_firebase_id_token(id_token_str: str) -> VerifiedFirebaseIdentity:
    """
    Cryptographically verify a Firebase ID token (signature, issuer, audience =
    the Firebase project id, expiry) via the Admin SDK, which caches Google's
    public keys locally after the first fetch. Still synchronous under the
    hood, so keep it off the event loop, same as verify_google_id_token.
    """
    app = _get_firebase_app()
    try:
        claims = await asyncio.to_thread(firebase_auth_sdk.verify_id_token, id_token_str, app=app)
    except Exception as e:
        # firebase_admin.auth raises several distinct exception subclasses
        # (ExpiredIdTokenError, InvalidIdTokenError, RevokedIdTokenError,
        # CertificateFetchError, etc.) — collapse to one type at this
        # boundary, same pattern as InvalidGoogleIdToken.
        raise InvalidFirebaseIdToken(str(e)) from e

    uid = claims.get("uid") or claims.get("sub")
    email = claims.get("email")
    if not uid or not email:
        raise InvalidFirebaseIdToken("Firebase token missing required claims (uid/email)")

    return VerifiedFirebaseIdentity(
        uid=uid,
        email=email,
        name=claims.get("name") or email,
        email_verified=bool(claims.get("email_verified", False)),
    )


async def delete_firebase_user(uid: str) -> None:
    """
    Deletes the Firebase Auth user identified by uid. Treats "already gone"
    as success — the account may have been partially deleted by a prior
    failed/retried delete-account attempt, and this needs to stay idempotent.
    """
    app = _get_firebase_app()
    try:
        await asyncio.to_thread(firebase_auth_sdk.delete_user, uid, app=app)
    except firebase_auth_sdk.UserNotFoundError:
        pass
