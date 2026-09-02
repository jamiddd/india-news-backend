"""Shared helper for calling APIVerve's game-generator APIs (crossword,
sudoku, word search, spelling bee, word ladder, trivia). Every caller wants
the same shape: GET/POST with an API key header, unwrap the {status, error,
data} envelope, and return None (never raise) on any failure so the caller
falls through to its existing curated/algorithmic fallback — same contract
as llm_gen.py's call_claude_json.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

BASE_URL = "https://api.apiverve.com/v1"


async def call_apiverve(
    endpoint: str,
    params: dict[str, Any] | None = None,
    *,
    method: str = "GET",
    timeout: float = 20,
    rate_limit_retries: int = 3,
) -> dict[str, Any] | None:
    """A single game-generation request (e.g. today's quiz) can mean several
    calls to this endpoint in a row (see daily_games.py's _apiverve_quiz),
    and a full run of the app across all games/quote-of-the-day can trip
    APIVerve's per-minute rate limit well before the monthly credit cap —
    observed in production as repeated 429s. Retry a 429 specifically, with
    backoff, rather than treating it like any other failure (which would
    make one busy minute silently degrade every game to its curated
    fallback for the rest of the day)."""
    if not settings.APIVERVE_API_KEY:
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
                if response.status_code == 429 and attempt < rate_limit_retries:
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
