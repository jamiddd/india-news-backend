"""Shared helper for calling APIVerve's game-generator APIs (crossword and
random quote — sudoku, word search, spelling bee, word ladder and trivia are
all generated locally now). Every caller wants the same shape: GET/POST with
an API key header, unwrap the {status, error, data} envelope, and return None
(never raise) on any failure so the caller falls through to its existing
curated/algorithmic fallback — same contract as llm_gen.py's call_claude_json.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

BASE_URL = "https://api.apiverve.com/v1"

# APIVerve reports the account's remaining monthly credits on every response,
# including the 429 that says they ran out. Stop calling before the balance
# actually hits zero: the number is only as fresh as the last response, and
# two callers racing on the last credit both see the same stale figure.
CREDIT_FLOOR = 5

# Set from the last response seen. None means "no response yet this process" —
# deliberately not treated as exhausted, or a fresh worker could never make its
# first call.
_remaining_credits: int | None = None


def remaining_credits() -> int | None:
    """Last-seen monthly credit balance, or None if nothing has been called
    yet in this process. Exposed for tests and diagnostics."""
    return _remaining_credits


def _record_credits(response: httpx.Response) -> None:
    global _remaining_credits
    raw = response.headers.get("x-api-remaining-credits")
    if raw is None:
        return
    try:
        _remaining_credits = int(raw)
    except ValueError:
        logger.warning("APIVerve sent an unparseable credit balance: %r", raw)


def _quota_exhausted(payload_error: object, response: httpx.Response) -> bool:
    """A 429 means one of two completely different things, and the status code
    alone cannot tell them apart:

      * monthly credits are gone   -> every later call fails until the billing
                                      period rolls over; retrying is useless
      * per-minute rate limit      -> transient, retrying with backoff works

    Both were confirmed in production on 2026-09-04: the credit 429 carries
    `"Monthly credit limit reached"` with `x-api-remaining-credits: -4` while
    `x-rate-limit-remaining` still had room. Treating the first like the second
    is what made an exhausted account burn three backoff sleeps per call and
    log nothing that explained why every game had gone to its fallback."""
    if "credit" in str(payload_error or "").casefold():
        return True
    raw = response.headers.get("x-api-remaining-credits")
    try:
        return raw is not None and int(raw) <= 0
    except ValueError:
        return False


async def call_apiverve(
    endpoint: str,
    params: dict[str, Any] | None = None,
    *,
    method: str = "GET",
    timeout: float = 20,
    rate_limit_retries: int = 3,
) -> dict[str, Any] | None:
    """One APIVerve request, or None on any failure.

    Credits are consumed by successful (HTTP 200) responses only — verified
    2026-09-04 by watching `x-api-remaining-credits` hold steady at -4 across
    repeated 429s. So a rejected call costs nothing but the rate-limit budget,
    and retrying a throttle is free; retrying an exhausted quota is not
    merely useless but hides the real cause behind a generic 429 log.
    """
    if not settings.APIVERVE_API_KEY:
        return None
    if _remaining_credits is not None and _remaining_credits <= CREDIT_FLOOR:
        logger.warning(
            "APIVerve %s skipped: %s credits remaining, at or below floor of %s",
            endpoint, _remaining_credits, CREDIT_FLOOR,
        )
        return None
    for attempt in range(rate_limit_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.request(
                    method,
                    f"{BASE_URL}/{endpoint}",
                    params=params if method == "GET" else None,
                    json=params if method != "GET" else None,
                    headers={"X-API-Key": settings.APIVERVE_API_KEY},
                )
                _record_credits(response)
                if response.status_code == 429:
                    # Read the body before deciding — see _quota_exhausted.
                    try:
                        error = response.json().get("error")
                    except ValueError:
                        error = response.text[:200]
                    if _quota_exhausted(error, response):
                        logger.error(
                            "APIVerve %s: monthly credits exhausted (%s remaining) — %s",
                            endpoint, response.headers.get("x-api-remaining-credits"), error,
                        )
                        return None
                    if attempt < rate_limit_retries:
                        wait = 2 ** (attempt + 1)
                        logger.info("APIVerve %s rate-limited, retrying in %ss", endpoint, wait)
                        await asyncio.sleep(wait)
                        continue
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("APIVerve %s call failed: %s: %s", endpoint, type(exc).__name__, exc)
            return None
        if payload.get("status") != "ok":
            logger.warning("APIVerve %s returned non-ok status: %s", endpoint, payload.get("error"))
            return None
        data = payload.get("data")
        return data if isinstance(data, dict) else None
    return None
