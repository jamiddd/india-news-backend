import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

from google.auth.transport import requests as google_auth_requests
from google.oauth2 import id_token as google_id_token

logger = logging.getLogger(__name__)

# google-auth's Request() wraps a requests.Session and caches Google's
# public certs internally — safe to share across calls.
_google_auth_request = google_auth_requests.Request()


@dataclass
class VerifiedGoogleIdentity:
    subject: str  # Google's stable per-account "sub" claim — the real identity key
    email: str
    name: str


class InvalidGoogleIdToken(Exception):
    pass


async def verify_google_id_token(id_token_str: str, audience: str) -> VerifiedGoogleIdentity:
    """
    Cryptographically verify a Google Sign-In ID token against Google's public
    keys (signature, issuer, audience, expiry) and return the claims we trust.

    Raises InvalidGoogleIdToken if the token is malformed, expired, signed for
    a different client, or the email isn't verified by Google.
    """
    try:
        # verify_oauth2_token does a (cached) network fetch of Google's certs
        # plus RSA signature verification — synchronous, so keep it off the
        # event loop.
        claims = await asyncio.to_thread(
            google_id_token.verify_oauth2_token,
            id_token_str,
            _google_auth_request,
            audience,
        )
    except ValueError as e:
        raise InvalidGoogleIdToken(str(e)) from e

    if not claims.get("email_verified", False):
        raise InvalidGoogleIdToken("Google account email is not verified")

    subject = claims.get("sub")
    email = claims.get("email")
    if not subject or not email:
        raise InvalidGoogleIdToken("Google token missing required claims (sub/email)")

    return VerifiedGoogleIdentity(
        subject=subject,
        email=email,
        name=claims.get("name") or email,
    )
