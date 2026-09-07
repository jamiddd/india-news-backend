"""LLM narrative generation for the Timeline/Context tab — shared by
scripts/test_timeline_narrative.py (the prompt-iteration harness that
validated this) and scripts/build_story_timelines.py (the production
generator). Extracted here rather than left in the test script once a
second caller needed it, so the prompt has one source of truth.

Validated 2026-09-07 against real chains: correctly narrates genuinely
connected sagas (ISRO privatisation standoff, US-Iran Gulf escalation) and
correctly rejects person-only-linkage blobs (a Rubio-linked chain spanning
unrelated policy tracks, a BCCI-linked chain spanning unrelated cricket
news) as coherent:false — see git history on this file's predecessor in
scripts/test_timeline_narrative.py for the full before/after prompt
iteration.
"""
from typing import Dict, List

import httpx

from app.config import settings
from app.services.story_chains import Cluster

MODEL = "claude-sonnet-5"
API_URL = "https://api.anthropic.com/v1/messages"

# Narrative writing needs real prose judgment (structure a saga into beats,
# decide what's connective-tissue vs. a genuine new development) — the
# cheap/fast Haiku used for the daily-games JSON extraction in llm_gen.py
# isn't the right default here the way it is there.

SYSTEM_PROMPT = """You are writing a "story so far" recap for a news app, in \
the voice of a YouTuber who covers one ongoing story across many videos and \
occasionally posts a big recap once enough has happened. The reader has NOT \
been following along — assume zero prior context, but do not pad with filler.

You will be given a chronological list of news events (each one a distinct \
story-cluster: a date, a headline, and outlet-sourced bullet summaries) that \
all belong to the same unfolding story. Turn them into ONE stitched \
narrative:

- A short "context" paragraph (2-4 sentences) that orients a reader with no \
  prior knowledge: who/what this is about and why it matters.
- A chronological list of "beats" — one entry per genuinely new development. \
  Merge events that are really the same beat reported differently; do not \
  pad the timeline to match the input count. Each beat: a plain-language date \
  reference, a one-line label, and 2-4 sentences of narration in your own \
  words (not copied summary bullets) that connects to what came before ("this \
  followed X", "in response to Y") rather than reading as an isolated blurb.
- No speculation about what happens next. No editorializing/opinion — narrate \
  what happened and why it's connected, not what should happen.
- If the events don't actually form one coherent trail (e.g. they only share \
  a name, not a throughline), say so plainly in "context" and return an empty \
  beats list rather than forcing a narrative.
- Specifically: a chain linked only by one recurring PERSON appearing in \
  each event, where the events themselves cover substantively unrelated \
  topics (e.g. the same official shows up in an unrelated trade dispute, a \
  personnel appointment, and a sanctions announcement, with no event \
  building on another's substance), is NOT a coherent story — mark \
  coherent:false, even if a superficial narrative COULD be written by \
  treating that person as the protagonist. A real story-so-far has events \
  that causally or substantively connect to each other, not merely a \
  cast member in common. When in doubt, prefer coherent:false to writing a \
  narrative that leans on a person's presence to paper over otherwise \
  disconnected events.

Respond with ONLY a JSON object, no markdown fences, matching exactly:
{
  "coherent": true | false,
  "title": "short headline for the whole trail",
  "context": "orienting paragraph",
  "beats": [
    {"date_label": "e.g. 'Early August' or 'Sept 3'", "label": "short beat title", "narration": "2-4 sentences", "cluster_ids": [123, 124]}
  ]
}"""


class TimelineNarrativeError(Exception):
    """Raised when Claude's reply can't be parsed as the expected JSON shape
    (usually a truncated response — see the stop_reason check below).
    Callers should treat this as "try again next cycle", not fatal."""


async def call_claude(user_content: str) -> dict:
    if not settings.ANTHROPIC_API_KEY:
        raise TimelineNarrativeError("ANTHROPIC_API_KEY not set in the environment.")
    async with httpx.AsyncClient(timeout=90) as client:
        resp = await client.post(
            API_URL,
            headers={
                "x-api-key": settings.ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": MODEL,
                "max_tokens": 8000,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": user_content}],
            },
        )
        resp.raise_for_status()
        data = resp.json()
        text = "".join(
            block.get("text", "") for block in data.get("content", []) if block.get("type") == "text"
        )
        cleaned = text.strip().strip("`").removeprefix("json").strip()
        try:
            import json
            return json.loads(cleaned)
        except Exception as e:
            # A long chain (many beats) can still outrun max_tokens even at
            # 8000 — surface the raw text and the stop_reason so a truncation
            # is legible as "ran out of tokens" rather than "malformed JSON".
            raise TimelineNarrativeError(
                f"JSON parse failed ({e}); stop_reason={data.get('stop_reason')}; raw={text[:2000]}"
            ) from e


def format_chain_for_prompt(members: List[Cluster], summaries: Dict[int, str]) -> str:
    lines = []
    for c in members:
        lines.append(f"--- cluster_id={c.id} | {c.first_seen_at.date().isoformat()} ---")
        lines.append(f"Headline: {c.headline}")
        summary = summaries.get(c.id)
        if summary:
            lines.append(f"Summary:\n{summary}")
        lines.append(f"Outlets covering: {c.distinct_source_count}")
    return "\n".join(lines)


async def fetch_summaries(session, cluster_ids: List[int]) -> Dict[int, str]:
    from sqlalchemy import text

    if not cluster_ids:
        return {}
    result = await session.execute(
        text("SELECT id, summary FROM story_clusters WHERE id = ANY(:ids)"),
        {"ids": cluster_ids},
    )
    return {row.id: row.summary for row in result if row.summary}


async def generate_narrative(session, members: List[Cluster]) -> dict:
    """Fetches summaries for `members` and calls Claude for the stitched
    narrative — the one call site both the test harness and the production
    generator should use, so they can never drift out of sync on how the
    prompt input is built."""
    summaries = await fetch_summaries(session, [m.id for m in members])
    prompt = format_chain_for_prompt(members, summaries)
    return await call_claude(prompt)
