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
from typing import Dict, List, Optional

import httpx

from app.config import settings
from app.services.story_chains import Cluster
from app.services.timeline_audio import SCRIPT_VERSION

MODEL = "claude-sonnet-5"
API_URL = "https://api.anthropic.com/v1/messages"

# Narrative writing needs real prose judgment (structure a saga into beats,
# decide what's connective-tissue vs. a genuine new development) — the
# cheap/fast Haiku used for the daily-games JSON extraction in llm_gen.py
# isn't the right default here the way it is there.

SPOKEN_SCRIPT_RULES = """A "spoken_script" is the same story rewritten to be read aloud by a \
text-to-speech voice: a single fixed male Indian-English voice that reads \
EXACTLY the words you write. The host is the presenter of a podcast called \
"Open Indian Voice". The voice has no emotion controls — its delivery comes \
only from your words and punctuation — so follow these rules precisely. They \
were tuned by ear over many listening rounds.

Shape (three parts):
- "intro": the spoken version of "context". Open with a warm greeting that \
  names the podcast, like "Heyyy everyone, welcome back to Open Indian \
  Voice!" (a close variant is fine, but it must name Open Indian Voice), then \
  hook the listener with a curiosity question about the story, then orient \
  them. Do not restate the whole context; keep it tight.
- "beats": an array parallel to the written beats — same order, same count, \
  same facts. Each is the spoken version of that beat's narration. Do not \
  invent facts beyond the written beat.
- "closing": one to two sentences summing up where things stand right now. \
  The LAST sentence is a sign-off that names the podcast, like "That's the \
  story we'll keep following, right here on Open Indian Voice." A summary of \
  what was said — no new development, no speculation, no opinion.
Keep each spoken beat about as long as its written beat, at most about 30% \
longer. The intro plus closing together stay short (a few sentences each).

Voice and tone: a casual, warm host who is part influencer, part storyteller. \
Tell the events in the PRESENT tense, the way a storyteller does ("Mundhe \
rolls out a calendar", "the F D A shuts the canteen"), even though the \
written beats are in the past tense. Contractions, plain words. Open some sentences as questions that the next \
sentence answers ("So, what happens when...?", "Why does it matter?", "So \
what happens next?"). Do NOT add reaction interjections ("Oh my God", "Wow", \
"Seriously", "Unreal", "guys"), and never write "phew". No all-caps words \
except at most one or two per story for real emphasis. Use exclamation marks \
sparingly — the greeting and an occasional turning point.

Sentence flow (the voice shapes intonation from punctuation, so this matters):
- Every sentence has at most two parts. Prefer one clear clause plus, at \
  most, one more. Split long, chained sentences into two.
- Use commas only at natural hinge points — after openers like "So,", "Now,", \
  "Then,", "Just days after X,", or before "and"/"but" joining two clauses. \
  Never more than two commas in a sentence besides filler commas.
- No semicolons, no em dashes, no colons in the middle of a sentence, no \
  parentheses, no bullet points, no markdown.
- Vary openings. Beats join with spoken transitions, not headers.

Dates, numbers, names:
- Every beat OPENS with a spoken date phrase built from that beat's date \
  label, in Indian day-before-month order and always in words: "On the third \
  of September," / "The very next day, the fourth of September," / "Two days \
  later, on the sixth of September,". Vary the phrasing beat to beat. Never \
  "September third". If the date label is loose ("Early August", "Late \
  2025"), say it naturally ("Early in August,").
- Write every number, year, percentage and amount as words: "twenty \
  twenty-six", "twelve thousand", "two thousand eight hundred and forty-one \
  percent", "one crore rupees". Use Indian units (lakh, crore) for rupees.
- Acronyms that are said letter by letter are written with a space between \
  the letters: "F D A", "I R C T C", "A I", "I P O", "C B I". Acronyms said as \
  words (ISRO, NASA, ISKCON, OPEC) stay as normal words. The voice rushes \
  unspaced letter-acronyms and mispronounces model/brand names: write those \
  the way they sound, e.g. "G P Teesix" for GPT-6.
- Give a plain-language gloss for any technical or domain term the stakes hinge on, \
  in the same or the next sentence; do not over-explain passing terms.
- No speculation and no editorializing — narrate what happened and why it is \
  connected.

Natural hesitations (use sparingly, they make the host sound human):
- Use only the two fillers "uhm" and "uh", always lowercase, in Latin \
  letters, wrapped in commas mid-sentence: "and, uh, led to...", "becomes, \
  uhm, an unlikely...". This is required, not optional: about one filler \
  per 650 characters of the whole script — a 5,000-character script has \
  about eight — spread evenly, at most one per beat, skipping a few beats. \
  If the input states a target filler count, hit it (give or take one). \
  Count your fillers before you answer and add more if you are below target — \
  writers tend to under-use them.
- A filler goes INSIDE a longer sentence, after its first few words. Never \
  start a sentence with a filler, never place one in a short sentence, never \
  put two close together, never in the greeting or the sign-off.

Do NOT write the trailing dots ("....", "......."), stage directions, or \
bracketed cues — the system adds the pauses itself.

Example of the approved register (a DIFFERENT story — imitate the rhythm, \
punctuation, spacing and fillers, never its facts):
INTRO: Heyyy everyone, welcome back to Open Indian Voice! So, what happens \
when one government officer decides to take food safety seriously? Let me \
take you back to May twenty twenty-six. Tukaram Mundhe takes over as \
Maharashtra's Food and Drug Administration commissioner, and he launches an \
aggressive, very public crackdown on hygiene violations across the state. \
It starts with routine inspections of street vendors and restaurants. And \
along the way, uhm, Mundhe becomes an unlikely pop-culture figure. So let's \
walk through the story.
BEAT: Two days later, on the sixth of September, Mundhe rolls out a \
year-round Festival Enforcement Calendar, aimed at adulteration during \
Ganeshotsav, Diwali, Eid and Christmas. He's careful to say it enforces the \
existing twenty eleven food safety law, not new festival restrictions. Then \
he gives an interview defending the campaign as driven by public health, and \
he reveals that since May, action has hit over twelve thousand \
establishments and, uh, led to seven hundred and forty pharmacy licence \
suspensions. Separately, the F D A presses five restaurants at Mumbai's M C A \
complex over unlicensed third-party operators running their kitchens.
BEAT: On the eighth of September, the F D A suspends the licences of a food \
supplier linked to I R C T C, and of three ISKCON eateries in Juhu. \
Inspectors found pest infestation, choked drains and improper food storage. \
ISKCON confirms the notice and says it's keeping the kitchens closed until \
the problems are fixed. No institution, however prominent, is being spared.
CLOSING: Tukaram Mundhe's crackdown has travelled from street vendors to \
movie stars since May. That's the story we'll keep following, right here on \
Open Indian Voice.
"""

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

Also write a SPOKEN version of the same narrative, for text-to-speech.

<<SPOKEN_SCRIPT_RULES>>

If coherent is false, omit spoken_script entirely.

Respond with ONLY a JSON object, no markdown fences, matching exactly:
{
  "coherent": true | false,
  "title": "short headline for the whole trail",
  "context": "orienting paragraph",
  "beats": [
    {"date_label": "e.g. 'Early August' or 'Sept 3'", "label": "short beat title", "narration": "2-4 sentences", "cluster_ids": [123, 124]}
  ],
  "spoken_script": {
    "intro": "spoken greeting + hook + orientation",
    "beats": ["spoken text for beat 0", "spoken text for beat 1"],
    "closing": "one to two sentence spoken wrap-up ending with the Open Indian Voice sign-off"
  }
}
Omit "spoken_script" (or set it to null) when coherent is false."""
SYSTEM_PROMPT = SYSTEM_PROMPT.replace("<<SPOKEN_SCRIPT_RULES>>", SPOKEN_SCRIPT_RULES)


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


SPOKEN_SCRIPT_ONLY_SYSTEM_PROMPT = """You are converting an already-written "story so far" recap \
into a SPOKEN script for text-to-speech.

You'll be given the written context paragraph and the written beats \
(already finalized — do not change their facts, order, or count).

<<SPOKEN_SCRIPT_RULES>>

Respond with ONLY a JSON object, no markdown fences, matching exactly:
{
  "intro": "spoken greeting + hook + orientation",
  "beats": ["spoken text for beat 0", "spoken text for beat 1"],
  "closing": "one to two sentence spoken wrap-up ending with the Open Indian Voice sign-off"
}"""
SPOKEN_SCRIPT_ONLY_SYSTEM_PROMPT = SPOKEN_SCRIPT_ONLY_SYSTEM_PROMPT.replace(
    "<<SPOKEN_SCRIPT_RULES>>", SPOKEN_SCRIPT_RULES
)


def finalize_spoken_script(script: Optional[dict], beat_count: int) -> Optional[dict]:
    """Validate a spoken_script Claude returned and stamp it with the version
    timeline_audio.generate_audio requires. Returns None (=> no audio for
    this story) when it is missing intro/beats, or when its beat count does
    not match the written beats — audio_beat_offsets must be exactly
    beat_count long or the app's beat highlighting misaligns. Never raises:
    a bad spoken_script must not fail narrative generation."""
    if not isinstance(script, dict):
        return None
    intro = script.get("intro")
    beats = script.get("beats")
    if not isinstance(intro, str) or not intro.strip() or not isinstance(beats, list):
        return None
    if len(beats) != beat_count or not all(isinstance(b, str) and b.strip() for b in beats):
        return None
    closing = script.get("closing")
    return {
        "intro": intro.strip(),
        "beats": [b.strip() for b in beats],
        "closing": closing.strip() if isinstance(closing, str) else "",
        "version": SCRIPT_VERSION,
    }


def _filler_target(context: str, beat_narrations: List[str]) -> int:
    """About one "uhm"/"uh" per 650 characters of the finished script, which
    runs roughly 20% longer than the written text it is converted from."""
    written = len(context) + sum(len(n) for n in beat_narrations)
    return max(1, round(written * 1.2 / 650))


async def call_claude_spoken_script_only(
    context: str, beats: List[dict], *, attempts: int = 3
) -> dict:
    """Backfill helper for rows whose spoken_script is missing or was written
    for the old Gemini voice (no "version" stamp). build_story_timelines.py
    skips the full narrative prompt when a chain has not changed, so such
    rows would never be rewritten on their own. Converts the existing written
    context/beats into a current-format spoken script without re-deciding
    the title/context/beats content, and returns it already validated and
    version-stamped (see finalize_spoken_script)."""
    if not settings.ANTHROPIC_API_KEY:
        raise TimelineNarrativeError("ANTHROPIC_API_KEY not set in the environment.")

    narrations = [b.get("narration", "") for b in beats]
    beat_lines = "\n\n".join(
        f"{i + 1}. Date label: {b.get('date_label') or '(none)'} | Title: {b.get('label') or ''}\n"
        f"Narration: {b.get('narration', '')}"
        for i, b in enumerate(beats)
    )
    user_content = (
        "Context:\n" + context + "\n\nBeats:\n" + beat_lines
        + f"\n\nTarget filler count for the whole script: {_filler_target(context, narrations)}."
    )
    beats = narrations  # from here on only the count matters (finalize_spoken_script)

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
            script = finalize_spoken_script(_json.loads(cleaned), len(beats))
        except Exception as e:
            last_error = TimelineNarrativeError(
                f"JSON parse failed ({e}); stop_reason={data.get('stop_reason')}; "
                f"usage={data.get('usage')}; raw={text[:2000]}"
            )
            continue
        if script is not None:
            return script
        last_error = TimelineNarrativeError(
            f"spoken script missing intro or its beat count != {len(beats)}; raw={text[:500]}"
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
    narrative = await call_claude(prompt)
    narrative["spoken_script"] = finalize_spoken_script(
        narrative.get("spoken_script"), len(narrative.get("beats") or [])
    )
    return narrative
