"""Verification and parsing for external (Razorpay/UPI) donation webhooks.

Kept out of main.py so the two things worth getting exactly right — the
signature check and the shape of the payment entity — are unit-testable
without a database or a live webhook.

Nothing here grants anything. Donations are collected outside Play Billing,
which is only permissible while the payment unlocks no app functionality; see
app/models.py's Donation.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

CAPTURED_EVENT = "payment.captured"

PAYMENT_LINKS_URL = "https://api.razorpay.com/v1/payment_links"

# Bounds on what the app may ask us to mint a link for. The client sends the
# amount, so it is not trustworthy: without a ceiling a tampered request could
# generate a link for an absurd sum and put our name on it. The floor is
# Razorpay's practical minimum for a card payment.
MIN_DONATION_PAISE = 100        # ₹1
MAX_DONATION_PAISE = 10_000_00  # ₹10,000


class MalformedWebhook(ValueError):
    """The signature was valid but the body wasn't a payment we can record."""


@dataclass(frozen=True)
class CapturedPayment:
    provider_payment_id: str
    amount_paise: int
    currency: str
    user_id: Optional[str]


def signature_matches(raw: bytes, signature: str, secret: str) -> bool:
    """Constant-time check of Razorpay's HMAC-SHA256 over the raw request body.

    Takes the raw bytes deliberately: re-serializing parsed JSON would change
    the byte sequence (key order, whitespace) and the digest with it, so the
    body must be verified before it is ever parsed.
    """
    expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


def parse_captured_payment(body: dict[str, Any]) -> Optional[CapturedPayment]:
    """Extracts the payment from a verified webhook body.

    Returns None for any event other than a capture (authorized, failed,
    refunded) — those are acknowledged and ignored rather than rejected, since
    a non-2xx would make Razorpay retry an event we will never store.

    Raises MalformedWebhook when it *is* a capture but the entity is unusable.
    """
    if body.get("event") != CAPTURED_EVENT:
        return None

    entity = (body.get("payload") or {}).get("payment", {}).get("entity") or {}
    payment_id = entity.get("id")
    amount = entity.get("amount")
    # bool is an int subclass, and `"amount": true` should not become ₹0.01.
    if not payment_id or not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
        raise MalformedWebhook("payment entity is missing a usable id/amount")

    # The payment page carries notes[user_id] so a donation can be attributed,
    # but donating while signed out is fine and common — a missing id means an
    # anonymous donation, not an invalid one.
    notes = entity.get("notes") or {}
    user_id = notes.get("user_id") or None
    if user_id is not None and not isinstance(user_id, str):
        user_id = None

    return CapturedPayment(
        provider_payment_id=str(payment_id)[:128],
        amount_paise=amount,
        currency=str(entity.get("currency") or "INR")[:8],
        user_id=user_id[:64] if user_id else None,
    )


async def create_payment_link(amount_paise: int, user_id: Optional[str]) -> Optional[str]:
    """Mints one Razorpay Payment Link and returns its payable short_url.

    A link per donation, rather than one static hosted Payment Page, purely so
    the donation can be attributed: Payment Pages accept no `notes`, and their
    URL prefill only reaches visible form fields. The user id rides in `notes`,
    which comes back on the payment entity in the webhook.

    Returns None on any failure — a donation that can't be started should show
    the user a plain "try again", never a stack trace, and never block on our
    problem with a third party.
    """
    if not settings.RAZORPAY_KEY_ID or not settings.RAZORPAY_KEY_SECRET:
        logger.warning("Donation link requested but Razorpay keys are not configured")
        return None

    body: dict[str, Any] = {
        "amount": amount_paise,
        "currency": "INR",
        "description": "Donation to Open Indian News",
        # We hold no verified phone/email for the donor and have no business
        # messaging them, so Razorpay must not notify or chase anyone.
        "notify": {"sms": False, "email": False},
        "reminder_enable": False,
        # Partial payments would leave a link half-settled and a payment row
        # that doesn't match the amount asked for. A donation is one payment.
        "accept_partial": False,
    }
    if user_id:
        body["notes"] = {"user_id": user_id}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                PAYMENT_LINKS_URL,
                json=body,
                auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET),
            )
        response.raise_for_status()
        return response.json().get("short_url")
    except Exception:
        # Deliberately broad: httpx timeouts, non-2xx, and malformed JSON all
        # mean the same thing to the caller.
        logger.exception("Failed to create Razorpay payment link")
        return None
