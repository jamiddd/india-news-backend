"""Out-of-band developer email alerts, independent of FCM/device tokens.

Added alongside the admin_alerts FCM topic (see admin_notify.py) after a
breaking review fired and reached nobody: the developer is frequently signed
out on debug installs, so no device token exists to push to. Email doesn't
depend on the phone, the app, or FCM being healthy at all — it's the
fallback channel that stays reachable even if both of those fail.

Uses Brevo's transactional email HTTP API, not SMTP: DigitalOcean blocks
outbound ports 25/465/587 account-wide on every droplet (confirmed
2026-09-16 — an SMTP version of this timed out on both newsapp and
newsapp-2), so any mail-server-style send is a dead end here regardless of
provider. Brevo's API runs over plain HTTPS (443), which isn't blocked.
Free tier (300/day, no expiry) is the same account earmarked for real
user-facing email later (donation thanks/welcome/policy).
"""
from __future__ import annotations

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

BREVO_ENDPOINT = "https://api.brevo.com/v3/smtp/email"


async def send_admin_email(subject: str, body: str) -> bool:
    """Best-effort developer alert email. Returns whether it was sent.

    No-ops (returns False) when BREVO_API_KEY/BREVO_SENDER_EMAIL/
    ADMIN_ALERT_EMAIL_TO aren't all configured, so this is inert on any
    machine without the secrets. Never raises — a notification failure must
    never touch the caller's own transaction, same posture as
    admin_notify._push_to_admin.
    """
    if not (settings.BREVO_API_KEY and settings.BREVO_SENDER_EMAIL and settings.ADMIN_ALERT_EMAIL_TO):
        return False

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                BREVO_ENDPOINT,
                headers={"api-key": settings.BREVO_API_KEY, "content-type": "application/json"},
                json={
                    "sender": {"email": settings.BREVO_SENDER_EMAIL},
                    "to": [{"email": settings.ADMIN_ALERT_EMAIL_TO}],
                    "subject": subject,
                    "textContent": body,
                },
            )
        response.raise_for_status()
        return True
    except Exception as e:
        # Log the exception type only — never the repr, which could echo the
        # API key back through an httpx error message.
        logger.warning(f"[AdminEmail] send failed: {type(e).__name__}")
        return False
