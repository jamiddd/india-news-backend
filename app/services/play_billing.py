"""
Server-side verification of Google Play Billing purchases.

A client's local "purchase succeeded" signal is not trustworthy on its own —
a rooted/tampered device can fake BillingClient's callback without ever
completing a real Play transaction (see BillingManager.kt's class doc). This
calls the Play Developer API directly with the purchase token the client
reports; only Google can mint that token, so verification here can't be
spoofed by anything running on the device.

Deliberately anonymous — no user_id involved. Premium in this app isn't tied
to app login (see NewsViewModel's local-only PremiumStatus flag), and
restore-across-reinstall/device already works via Play's own account system
(BillingManager.restorePurchases), independent of this backend. This module
only answers "is this specific token real and currently paid for", nothing
more — it does not persist an entitlement ledger.
"""
import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

from app.config import settings

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/androidpublisher"]
_API_BASE = "https://androidpublisher.googleapis.com/androidpublisher/v3/applications"

_session: Optional[AuthorizedSession] = None


class PlayBillingNotConfigured(Exception):
    pass


def _get_session() -> AuthorizedSession:
    """Lazily build the authorized session on first use, same reasoning as
    firebase_auth._get_firebase_app: a missing/misconfigured credential
    shouldn't crash module import for code paths that don't need it yet."""
    global _session
    if _session is None:
        if not settings.GOOGLE_PLAY_SERVICE_ACCOUNT_PATH:
            raise PlayBillingNotConfigured("GOOGLE_PLAY_SERVICE_ACCOUNT_PATH not configured")
        credentials = service_account.Credentials.from_service_account_file(
            settings.GOOGLE_PLAY_SERVICE_ACCOUNT_PATH, scopes=_SCOPES
        )
        _session = AuthorizedSession(credentials)
    return _session


@dataclass
class PurchaseVerification:
    valid: bool
    reason: str  # human-readable, safe to log; never shown to the end user


def _verify_subscription_sync(package_name: str, purchase_token: str) -> PurchaseVerification:
    session = _get_session()
    url = f"{_API_BASE}/{package_name}/purchases/subscriptionsv2/tokens/{purchase_token}"
    resp = session.get(url, timeout=10)
    if resp.status_code != 200:
        return PurchaseVerification(False, f"subscriptionsv2 lookup failed: {resp.status_code} {resp.text[:200]}")
    state = resp.json().get("subscriptionState")
    # ACTIVE and IN_GRACE_PERIOD are the only states where the subscriber is
    # actually entitled right now — cancelled/expired/on-hold/paused/pending
    # all mean no active premium access.
    if state in ("SUBSCRIPTION_STATE_ACTIVE", "SUBSCRIPTION_STATE_IN_GRACE_PERIOD"):
        return PurchaseVerification(True, state)
    return PurchaseVerification(False, f"subscription not active: {state}")


def _verify_product_sync(package_name: str, product_id: str, purchase_token: str) -> PurchaseVerification:
    session = _get_session()
    url = f"{_API_BASE}/{package_name}/purchases/products/{product_id}/tokens/{purchase_token}"
    resp = session.get(url, timeout=10)
    if resp.status_code != 200:
        return PurchaseVerification(False, f"products lookup failed: {resp.status_code} {resp.text[:200]}")
    # purchaseState: 0 = purchased, 1 = cancelled, 2 = pending.
    purchase_state = resp.json().get("purchaseState")
    if purchase_state == 0:
        return PurchaseVerification(True, "purchased")
    return PurchaseVerification(False, f"purchase not completed: purchaseState={purchase_state}")


async def verify_purchase(product_id: str, purchase_token: str, product_type: str) -> PurchaseVerification:
    """product_type is "subs" or "inapp", matching BillingClient.ProductType
    on the client. Runs the underlying (synchronous) HTTP call off the event
    loop, same pattern as verify_firebase_id_token."""
    package_name = settings.ANDROID_PACKAGE_NAME
    try:
        if product_type == "subs":
            return await asyncio.to_thread(_verify_subscription_sync, package_name, purchase_token)
        elif product_type == "inapp":
            return await asyncio.to_thread(_verify_product_sync, package_name, product_id, purchase_token)
        return PurchaseVerification(False, f"unknown product_type: {product_type}")
    except PlayBillingNotConfigured:
        raise
    except Exception as e:
        logger.exception("Play purchase verification failed")
        return PurchaseVerification(False, f"verification error: {e}")
