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
    model: str = MODEL,
    max_tokens: int = 2000,
    temperature: float | None = 0.8,
    disable_thinking: bool = False,
    effort: str | None = None,
    attempts: int = 3,
    timeout: float = 45,
) -> dict[str, Any] | None:
    """Call Claude, parse its reply as JSON, and return it — or None once all
    attempts are exhausted. Callers are expected to validate the shape of the
    returned dict themselves (this only guarantees "valid JSON", not "valid
    puzzle") and to fall back to a deterministic generator on None or on a
    validation failure. `model` defaults to the cheap/fast MODEL used by every
    daily game except crossword, which overrides it — crossword's exact
    11x11-grid-plus-180-degree-symmetry constraint is a harder structured
    generation task than the other games' looser letter/word-list puzzles,
    and Haiku was found to fail validation on it consistently (see
    crossword.py's generate_puzzle).
    """
    if not settings.ANTHROPIC_API_KEY:
        return None
    for attempt in range(attempts):
        try:
            payload: dict[str, Any] = {
                "model": model,
                "max_tokens": max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": user_content}],
            }
            if temperature is not None:
                # Claude Sonnet 5 (and other current-gen models) reject a
                # non-default temperature with 400 invalid_request_error.
                # Only send it for models that accept it (e.g. Haiku).
                payload["temperature"] = temperature
            if disable_thinking:
                # Sonnet 5 runs adaptive thinking by default, which eats
                # into max_tokens for a plain JSON-extraction task and adds
                # a "thinking" block ahead of "text" in the response.
                payload["thinking"] = {"type": "disabled"}
            if effort is not None:
                # Bounds adaptive thinking depth (low/medium/high/xhigh/max)
                # so a task that benefits from reasoning (e.g. crossword's
                # symmetry check) doesn't burn the whole max_tokens budget
                # on unbounded thinking with nothing left for output.
                payload["output_config"] = {"effort": effort}
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    API_URL,
                    headers={
                        "x-api-key": settings.ANTHROPIC_API_KEY,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json=payload,
                )
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    logger.warning(
                        "Claude JSON generation attempt %s failed: %s %s — body: %s",
                        attempt + 1, exc.response.status_code, type(exc).__name__,
                        exc.response.text[:2000],
                    )
                    continue
                raw = response.json().get("content") or []
                # Sonnet 5 (and any model run with thinking on) can put a
                # "thinking" block before the "text" block, so don't assume
                # raw[0] is text — find the first text block explicitly.
                text_block = next((block for block in raw if block.get("type") == "text"), None)
                if text_block is None:
                    raise ValueError(f"No text block in response content: {raw!r}")
                return parse_json_response(text_block["text"])
        except Exception as exc:
            logger.warning(
                "Claude JSON generation attempt %s failed: %s: %s",
                attempt + 1, type(exc).__name__, exc,
            )
    return None
