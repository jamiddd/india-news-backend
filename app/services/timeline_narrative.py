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

Also write a SPOKEN version of the same narrative, for text-to-speech — a \
"spoken_script" with an "intro" (spoken version of "context") and a "beats" \
array parallel to the beats list above (same order, same count). This is a \
radio/podcast host talking a listener through the saga, not a screen reader \
and not a flat recap read in a monotone:
- Host energy: an intro that hooks the listener in rather than just stating \
  facts ("Alright, so this one's been building for weeks — let's get into \
  it" is the kind of register to aim for, not that exact line every time). \
  Rhetorical questions and asides are welcome ("So what actually happened \
  next?", "Here's the part that got everyone's attention"). Vary pacing \
  the way a host would for emphasis — short punchy sentences at a turning \
  point, longer ones when laying out context.
- Write for the ear, not the page. Contractions ("it's", "here's"). Varied \
  sentence length. Spoken transitions between beats ("so here's where it \
  gets interesting…", "now, back up a second…", "then, a few days later…") \
  instead of restating each beat's date label as a header.
- Expand numbers, dates, acronyms, and abbreviations into how a person would \
  say them aloud (e.g. "the ISRO", "September third", "twenty percent").
- No markdown, no bullet points, no parenthetical asides — this is spoken \
  prose only.
- Write for a listener with no background in the story's domain — not just \
  no prior knowledge of these specific events, but no assumed familiarity \
  with the field's jargon either (this applies to any domain: finance, \
  science, law, sports, politics — not only tech). Whenever a beat leans on \
  a technical or domain-specific term to carry the actual stakes of what \
  happened (why something matters or is risky/significant), gloss it in \
  plain language in the same sentence or the one right after — don't just \
  name the term and move on. A listener should understand WHY something \
  matters, not just be told that it does. Do not over-explain terms that \
  carry no real weight in the story (e.g. a passing acronym for an \
  organization named once) — only gloss the ones the narrative's stakes \
  actually hinge on.
- Follow all the same content rules as the written version: no speculation, \
  no editorializing. If coherent is false, omit spoken_script entirely.
- After the last beat, write a "closing" — one to two sentences that sum up \
  where things stand right now, the way a radio host signs off a segment \
  ("and that's where it stands right now" / "so that's the story, at least \
  for the moment"). This is a summary of what was just said, not a new \
  development, not speculation about what happens next, and not an opinion.

Respond with ONLY a JSON object, no markdown fences, matching exactly:
{
  "coherent": true | false,
  "title": "short headline for the whole trail",
  "context": "orienting paragraph",
  "beats": [
    {"date_label": "e.g. 'Early August' or 'Sept 3'", "label": "short beat title", "narration": "2-4 sentences", "cluster_ids": [123, 124]}
  ],
  "spoken_script": {
    "intro": "spoken version of the context paragraph",
    "beats": ["spoken text for beat 0", "spoken text for beat 1"],
    "closing": "one to two sentence spoken wrap-up summing up where things stand"
  }
}
Omit "spoken_script" (or set it to null) when coherent is false."""


class TimelineNarrativeError(Exception):
    """Raised when Claude's reply can't be parsed as the expected JSON shape
    (usually a truncated response — see the stop_reason check below).
    Callers should treat this as "try again next cycle", not fatal."""


async def call_claude(user_content: str, *, attempts: int = 3) -> dict:
    """Adding spoken_script to the required JSON made a truncated/malformed
    reply more likely (more output tokens, more structure to get right), so
    this retries transient failures instead of raising on the first bad
    response — callers still see TimelineNarrativeError only once all
    attempts are exhausted, so the "try again next cycle" contract is
    unchanged."""
    if not settings.ANTHROPIC_API_KEY:
        raise TimelineNarrativeError("ANTHROPIC_API_KEY not set in the environment.")

    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
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
                        # Raised from 8000 after a real 18-member chain hit
                        # max_tokens on all 3 retry attempts identically —
                        # spoken_script roughly doubles output size on top
                        # of beats, so a large chain can systematically
                        # outrun the old ceiling rather than just
                        # occasionally flake (see the JSON-parse-failure
                        # comment below).
                        "max_tokens": 16000,
                        # Sonnet 5 runs adaptive thinking by default, and
                        # thinking tokens draw from the SAME max_tokens
                        # budget before any output text is written — a
                        # 19-member chain still truncated even at 16000
                        # because thinking length varies unpredictably per
                        # call and can eat most of the budget on a hard
                        # coherence judgment. Bounding it to "medium" (this
                        # task genuinely benefits from some reasoning — see
                        # the module docstring on why Haiku isn't used here
                        # — just not unbounded reasoning) leaves output
                        # budget for the JSON large enough for max_tokens to
                        # actually be about output size again.
                        "output_config": {"effort": "medium"},
                        "system": SYSTEM_PROMPT,
                        "messages": [{"role": "user", "content": user_content}],
                    },
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as e:
            last_error = e
            continue

        text = "".join(
            block.get("text", "") for block in data.get("content", []) if block.get("type") == "text"
        )
        cleaned = text.strip().strip("`").removeprefix("json").strip()
        try:
            import json
            return json.loads(cleaned)
        except Exception as e:
            # A long chain (many beats) can still outrun max_tokens — surface
            # the raw text, stop_reason, and token usage (thinking vs.
            # output split) so a truncation is legible as "ran out of
            # tokens" (and specifically whether thinking or output ate the
            # budget) rather than "malformed JSON".
            last_error = TimelineNarrativeError(
                f"JSON parse failed ({e}); stop_reason={data.get('stop_reason')}; "
                f"usage={data.get('usage')}; raw={text[:2000]}"
            )

    raise last_error if isinstance(last_error, TimelineNarrativeError) else TimelineNarrativeError(
        f"call_claude failed after {attempts} attempts: {last_error}"
    )


CLOSING_SYSTEM_PROMPT = """You are writing the closing line of a spoken \
"story so far" recap, in the same voice as the rest of it — a radio/podcast \
host wrapping up a segment, not a screen reader.

You'll be given the intro and the beat-by-beat narration already written. \
Write ONE to TWO sentences that sum up where things stand right now, the \
way a host signs off a segment ("and that's where it stands right now" / \
"so that's the story, at least for the moment"). This is a summary of what \
was just said — not a new development, not speculation about what happens \
next, and not an opinion.

Follow the same spoken-prose rules as the rest of the script: contractions, \
no markdown, no bullet points, no parenthetical asides, numbers/acronyms \
expanded the way a person would say them aloud.

Respond with ONLY the closing text itself — no quotes, no JSON, no preamble."""


async def call_claude_closing(intro: str, beats: List[str], *, attempts: int = 3) -> str:
    """One-off backfill helper: existing rows already have a full
    spoken_script (intro + beats) from before the "closing" field existed.
    Re-running the full narrative prompt just to add a closing line would
    re-spend an LLM call on title/context/beats that haven't changed and
    risk them drifting slightly on a reroll — this asks for only the
    missing piece instead, using the SAME writing-style rules extracted
    into CLOSING_SYSTEM_PROMPT above (kept in sync with the "closing"
    guidance inside SYSTEM_PROMPT for new rows)."""
    if not settings.ANTHROPIC_API_KEY:
        raise TimelineNarrativeError("ANTHROPIC_API_KEY not set in the environment.")

    user_content = (
        "Intro:\n" + intro + "\n\nBeats:\n" + "\n\n".join(f"{i + 1}. {b}" for i, b in enumerate(beats))
    )

    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    API_URL,
                    headers={
                        "x-api-key": settings.ANTHROPIC_API_KEY,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": MODEL,
                        "max_tokens": 500,
                        "system": CLOSING_SYSTEM_PROMPT,
                        "messages": [{"role": "user", "content": user_content}],
                    },
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as e:
            last_error = e
            continue
        text = "".join(
            block.get("text", "") for block in data.get("content", []) if block.get("type") == "text"
        )
        cleaned = text.strip().strip('"').strip()
        if cleaned:
            return cleaned
        last_error = TimelineNarrativeError(f"empty closing text; stop_reason={data.get('stop_reason')}")

    raise last_error if isinstance(last_error, TimelineNarrativeError) else TimelineNarrativeError(
        f"call_claude_closing failed after {attempts} attempts: {last_error}"
    )


SPOKEN_SCRIPT_ONLY_SYSTEM_PROMPT = """You are writing a SPOKEN version of an \
already-written "story so far" recap, for text-to-speech — a radio/podcast \
host talking a listener through the saga, not a screen reader and not a \
flat recap read in a monotone.

You'll be given the written context paragraph and the written beats \
(already finalized — do not change their facts, order, or count). Produce:
- an "intro": the spoken version of the context paragraph
- a "beats" array, same order and count as the beats given, each a spoken \
  version of that beat's narration
- a "closing": one to two sentences summing up where things stand right \
  now, the way a host signs off a segment. This is a summary of what was \
  just said, not a new development, not speculation, not an opinion.

Writing rules for all of the above:
- Host energy: an intro that hooks the listener in rather than just stating \
  facts. Rhetorical questions and asides are welcome ("So what actually \
  happened next?", "Here's the part that got everyone's attention"). Vary \
  pacing the way a host would for emphasis — short punchy sentences at a \
  turning point, longer ones when laying out context.
- Write for the ear, not the page. Contractions ("it's", "here's"). Varied \
  sentence length. Spoken transitions between beats ("so here's where it \
  gets interesting…", "now, back up a second…", "then, a few days later…") \
  instead of restating each beat's date label as a header.
- Expand numbers, dates, acronyms, and abbreviations into how a person would \
  say them aloud (e.g. "the ISRO", "September third", "twenty percent").
- No markdown, no bullet points, no parenthetical asides — spoken prose only.
- Write for a listener with no background in the story's domain. Whenever a \
  beat leans on a technical or domain-specific term to carry the actual \
  stakes of what happened, gloss it in plain language in the same sentence \
  or the one right after. Do not over-explain terms that carry no real \
  weight in the story.
- No speculation, no editorializing.

Respond with ONLY a JSON object, no markdown fences, matching exactly:
{
  "intro": "spoken version of the context paragraph",
  "beats": ["spoken text for beat 0", "spoken text for beat 1"],
  "closing": "one to two sentence spoken wrap-up summing up where things stand"
}"""


async def call_claude_spoken_script_only(
    context: str, beats: List[str], *, attempts: int = 3
) -> dict:
    """One-off backfill helper for rows that predate the spoken_script
    feature entirely (chain hasn't changed since before spoken_script was
    added, so build_story_timelines.py's "skip if unchanged" logic never
    re-ran the full narrative prompt for them). Converts the existing
    written context/beats into a spoken script without re-deciding
    title/context/beats content, mirroring call_claude_closing's
    "don't re-spend on what hasn't changed" reasoning one level up."""
    if not settings.ANTHROPIC_API_KEY:
        raise TimelineNarrativeError("ANTHROPIC_API_KEY not set in the environment.")

    user_content = (
        "Context:\n" + context + "\n\nBeats:\n" + "\n\n".join(f"{i + 1}. {b}" for i, b in enumerate(beats))
    )

    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
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
                        "max_tokens": 16000,
                        "output_config": {"effort": "medium"},
                        "system": SPOKEN_SCRIPT_ONLY_SYSTEM_PROMPT,
                        "messages": [{"role": "user", "content": user_content}],
                    },
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as e:
            last_error = e
            continue

        text = "".join(
            block.get("text", "") for block in data.get("content", []) if block.get("type") == "text"
        )
        cleaned = text.strip().strip("`").removeprefix("json").strip()
        try:
            import json as _json
            return _json.loads(cleaned)
        except Exception as e:
            last_error = TimelineNarrativeError(
                f"JSON parse failed ({e}); stop_reason={data.get('stop_reason')}; "
                f"usage={data.get('usage')}; raw={text[:2000]}"
            )

    raise last_error if isinstance(last_error, TimelineNarrativeError) else TimelineNarrativeError(
        f"call_claude_spoken_script_only failed after {attempts} attempts: {last_error}"
    )


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
