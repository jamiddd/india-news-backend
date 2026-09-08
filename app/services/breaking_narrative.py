"""LLM developing-vs-echo judgement + beat extraction for the "Breaking"
slot. Sibling of app/services/timeline_narrative.py, not a reuse of it: that
module narrates a CHAIN OF CLUSTERS over weeks; this one narrates ONE
CLUSTER'S ARTICLES over hours — different input shape, so the beats have to
come from timestamped article headlines, not cluster summaries.

Validated by hand against cluster 46759 (Nepal flood, 90 articles) 2026-09-08:
distinct new facts land on a clear cadence — rescue, survivor account, toll
revisions, further rescues, mourning declared, trapped-count corrected
900->121 — roughly 8-9 real beats, the rest genuine echo (10-25 outlets
repeating one fact within an hour of it breaking). See
scripts/check_breaking_candidates.py --cluster 46759 for the raw input this
prompt receives.

Two guards baked in, both lessons from prior incidents on this codebase
(see backend/docs/clustering-rework-handoff.md):

1. Citation check — every beat must cite article_ids that exist in the
   cluster; verify_beats() drops any that don't. Turns a hallucinated beat
   into a silently-dropped one, not a false claim shown to a user.
2. `developing: false` is a first-class, expected outcome, not a failure —
   same posture as timeline_narrative.py's `coherent: false`. Callers must
   write status='rejected' and promote nothing.
"""
from typing import Dict, List, Optional

import httpx

from app.config import settings

MODEL = "claude-sonnet-5"
API_URL = "https://api.anthropic.com/v1/messages"

SYSTEM_PROMPT = """You are a wire editor deciding whether a fast-moving news \
story is still actively developing, and if so, extracting its real \
timeline.

You will be given one cluster of news articles — all about the same event \
— as a chronological list of (timestamp, outlet, headline) rows. A single \
event can attract 20+ outlets within hours, and the large majority of them \
are simply reporting the SAME fact within a short window of each other, not \
adding anything new.

Your job:

1. Decide: is genuinely NEW information still emerging over time (a rescue, \
   a revised death toll, an official statement, a newly confirmed detail), \
   or are these articles substantively just the same one or two facts \
   reworded by many outlets? If nearly everything clusters into one or two \
   moments with no real progression, this is NOT developing.

2. If developing, extract the DISTINCT beats — one per genuinely new fact, \
   in chronological order. A beat is NOT "an outlet posted about the story \
   again" — it is "a new fact appeared that wasn't in any earlier beat". \
   Merge every article that reports the same fact (even with a different \
   angle, quote, or headline spin) into the ONE beat that fact belongs to. \
   A burst of 10-25 outlets within an hour of each other reporting the same \
   development is normal and should collapse into a SINGLE beat, not one \
   beat per outlet.

3. For each beat, cite the article_ids (from the numbered list you're given) \
   that reported that specific fact — every one of them, so a citation can \
   be verified against the source list. Do not cite an id for a beat it \
   doesn't actually support.

4. Write a short, plain-language narration (1-3 sentences) per beat in your \
   own words — what happened, not a copy of any single headline.

Do not pad the timeline to look more "breaking" than it is. A story with \
90 articles but only 2 real facts should return exactly 2 beats and, if \
that's genuinely all there is, developing:false — a big number of outlets \
is not by itself "developing".

Respond with ONLY a JSON object, no markdown fences, matching exactly:
{
  "developing": true | false,
  "title": "short headline for the developing story, or empty string if not developing",
  "beats": [
    {"time_label": "e.g. 'Sept 4, early morning' or 'Sept 5, midday'", "label": "short beat title", "narration": "1-3 sentences", "article_ids": [123, 124]}
  ]
}"""

# Same shape as timeline_narrative.py's incremental-refresh instruction —
# appended only when extending an existing timeline, never on first
# generation, so a first pass and a refresh share one system prompt.
REFRESH_SUFFIX = """

You are EXTENDING an existing timeline, not writing it from scratch. You \
will also be given the beats already established. Only add beats for \
genuinely new facts found in the NEW articles below the existing beats — do \
not rewrite, merge into, or reword any existing beat. If none of the new \
articles add a genuinely new fact (they're more echo of what's already \
covered), return the existing beats unchanged and developing:true (a quiet \
patch doesn't undevelop an already-developing story)."""


class BreakingNarrativeError(Exception):
    """Raised when Claude's reply can't be parsed as the expected JSON shape
    — callers should treat this as "try again next cycle", not fatal, same
    posture as TimelineNarrativeError."""


async def call_claude(system_prompt: str, user_content: str) -> dict:
    if not settings.ANTHROPIC_API_KEY:
        raise BreakingNarrativeError("ANTHROPIC_API_KEY not set in the environment.")
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
                "max_tokens": 4000,
                "system": system_prompt,
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
            raise BreakingNarrativeError(
                f"JSON parse failed ({e}); stop_reason={data.get('stop_reason')}; raw={text[:2000]}"
            ) from e


def format_articles_for_prompt(articles: List[dict]) -> str:
    """`articles`: [{"id": int, "published_at": datetime, "source_name": str,
    "title": str}], already in published_at order. Same
    time | outlet | title shape as scripts/check_breaking_candidates.py's
    --cluster dump, so what's eyeballed during validation is exactly what
    the model receives."""
    lines = []
    for a in articles:
        lines.append(f"[{a['id']}] {a['published_at'].isoformat()} | {a['source_name']} | {a['title']}")
    return "\n".join(lines)


def verify_beats(beats: List[dict], valid_article_ids: set) -> List[dict]:
    """Drops any beat with zero valid citations, and strips invalid ids from
    beats that have at least one valid one — a hallucinated id becomes a
    silent omission, never a claim the client shows as true. See this
    module's docstring."""
    verified = []
    for beat in beats:
        cited = [aid for aid in beat.get("article_ids", []) if aid in valid_article_ids]
        if not cited:
            continue
        verified.append({**beat, "article_ids": cited})
    return verified


async def judge_and_extract_beats(
    articles: List[dict],
    existing_beats: Optional[List[dict]] = None,
) -> dict:
    """Runs the developing-vs-echo + beat-extraction pass. `articles` is the
    full set for a first pass, or only the NEW articles since the last
    generation for a refresh (with `existing_beats` passed alongside — see
    REFRESH_SUFFIX).

    Returns {"developing": bool, "title": str, "beats": [...]} with beats
    already run through verify_beats() against this call's `articles`.
    Callers doing a refresh should merge the returned beats onto
    `existing_beats` themselves (this function doesn't assume how the
    caller stores them).
    """
    valid_ids = {a["id"] for a in articles}
    system_prompt = SYSTEM_PROMPT
    user_content = format_articles_for_prompt(articles)

    if existing_beats:
        system_prompt = SYSTEM_PROMPT + REFRESH_SUFFIX
        existing_block = "\n".join(
            f"- {b['time_label']}: {b['label']} — {b['narration']}" for b in existing_beats
        )
        user_content = (
            f"EXISTING BEATS:\n{existing_block}\n\n"
            f"NEW ARTICLES (since the last pass):\n{user_content}"
        )

    result = await call_claude(system_prompt, user_content)
    result["beats"] = verify_beats(result.get("beats") or [], valid_ids)
    return result
