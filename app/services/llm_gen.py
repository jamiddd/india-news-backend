"""Shared helper for the daily-games generators (crossword, word search,
spelling bee, word ladder, quiz). Every content-driven game follows the same
shape: ask Claude for JSON, validate it in Python, and fall back to a
deterministic algorithmic/curated puzzle if the model call or validation
fails. This module owns the one HTTP call site so that shape stays uniform
instead of each service re-implementing its own httpx/Claude/JSON-parsing
boilerplate (which crossword.py, enrichment.py and polls.py used to do
independently).
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings
from app.services.enrichment import parse_json_response

logger = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5"
API_URL = "https://api.anthropic.com/v1/messages"


async def call_claude_json(
    system: str,
    user_content: str,
    *,
    max_tokens: int = 2000,
    temperature: float = 0.8,
    attempts: int = 3,
    timeout: float = 45,
) -> dict[str, Any] | None:
    """Call Claude, parse its reply as JSON, and return it — or None once all
    attempts are exhausted. Callers are expected to validate the shape of the
    returned dict themselves (this only guarantees "valid JSON", not "valid
    puzzle") and to fall back to a deterministic generator on None or on a
    validation failure.
    """
    if not settings.ANTHROPIC_API_KEY:
        return None
    for attempt in range(attempts):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    API_URL,
                    headers={
                        "x-api-key": settings.ANTHROPIC_API_KEY,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": MODEL,
                        "max_tokens": max_tokens,
                        "temperature": temperature,
                        "system": system,
                        "messages": [{"role": "user", "content": user_content}],
                    },
                )
                response.raise_for_status()
                raw = response.json().get("content") or []
                if not raw:
                    raise ValueError("Empty response content")
                return parse_json_response(raw[0]["text"])
        except Exception as exc:
            logger.warning("Claude JSON generation attempt %s failed: %s", attempt + 1, exc)
    return None
