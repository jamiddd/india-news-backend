"""Shared helper for calling APIVerve's game-generator APIs (crossword,
sudoku, word search, spelling bee, word ladder, trivia). Every caller wants
the same shape: GET/POST with an API key header, unwrap the {status, error,
data} envelope, and return None (never raise) on any failure so the caller
falls through to its existing curated/algorithmic fallback — same contract
as llm_gen.py's call_claude_json.
"""
from __future__ import annotations

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
) -> dict[str, Any] | None:
    if not settings.APIVERVE_API_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(
                method,
                f"{BASE_URL}/{endpoint}",
                params=params if method == "GET" else None,
                json=params if method != "GET" else None,
                headers={"X-API-Key": settings.APIVERVE_API_KEY},
            )
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
