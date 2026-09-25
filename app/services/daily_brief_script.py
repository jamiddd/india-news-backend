"""Claude writes the Daily Brief: a one-line on-screen summary and a short
spoken paragraph for each selected story, plus an intro and closing.

Input is headlines only (the story's headline and up to a few article titles)
— no article text, so Claude can only rephrase what those headlines say, and
the prompt forbids adding anything else. The spoken style follows the timeline
narration rules (timeline_narrative.SPOKEN_SCRIPT_RULES), trimmed for a
rundown: the same host and voice, the same punctuation/number/acronym rules,
because the Sarvam voice is tuned to that style (see timeline_audio.py).
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from app.services.daily_brief_select import BriefStory
from app.services.llm_gen import call_claude_json
from app.services.timeline_audio import SCRIPT_VERSION, SIGN_OFF

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-5"
ATTEMPTS = 2
# About one "uhm"/"uh" per 650 characters, same as the timeline scripts.
TARGET_CHARS = 2700

SYSTEM_PROMPT = """You write "Your Daily Brief", the morning news rundown of a \
podcast called "Open Indian Voice". You are given yesterday's top stories, each \
with a headline and the titles of some articles about it. That is ALL you know: \
never add a fact, name, number, cause, reaction or year that is not in the \
headlines or titles. When the headlines are thin, say less, do not fill in.

Return ONLY a JSON object:
{"intro": "...", "items": [{"cluster_id": <int>, "summary": "...", "spoken": "..."}, ...], "closing": "..."}
"items" must contain EVERY story you were given, in the SAME order, with its \
cluster_id copied exactly.

"summary" is for the screen: one plain sentence of about twenty words, no more \
than twenty-five, past tense, neutral, no markdown. Ordinary digits are fine.

"spoken" is read aloud by a single fixed male Indian-English text-to-speech \
voice that reads EXACTLY the words you write. About thirty-five to forty-five \
words per story, two or three sentences. Rules:
- Casual, warm host in the present tense ("Parliament passes...", "the R B I \
  holds rates..."). Say "yesterday" if a time is needed; never name a date or a \
  year. Contractions and plain words. No interjections ("Wow", "Oh my God"), \
  never "phew", no all-caps words.
- Each sentence has at most two parts. Commas only at natural hinge points. No \
  semicolons, em dashes, colons, parentheses, bullet points or markdown.
- Start each story with a very short spoken transition that varies ("First up,", \
  "Meanwhile,", "In business,", "On the sports front,", "And in tech,"). The \
  first story needs no transition beyond the intro.
- Write EVERY number, amount and percentage in words ("twelve thousand", "two \
  lakh crore rupees"), with no digits at all. Indian units for rupees.
- Letter-by-letter acronyms are written with spaces ("B J P", "R B I", "A I", \
  "I P O"). Acronyms said as words (ISRO, NASA, OPEC) stay as words. Write \
  model and brand names the way they sound.
- No speculation, no opinion, no editorializing.
- Use only the fillers "uhm" and "uh", lowercase, wrapped in commas INSIDE a \
  longer sentence after its first few words ("and, uh, the..."). Use exactly \
  <<FILLERS>> fillers in the whole script, spread out, at most one per story, \
  never in the intro greeting, the closing, or a short sentence.
- Never write trailing dots, stage directions or bracketed cues.

"intro": a warm greeting that names the podcast and this being the brief for \
the day, like "Good morning, and welcome to your Daily Brief on Open Indian \
Voice!", then one sentence saying what is coming ("Here's what mattered \
yesterday."). At most about 220 characters.

"closing": one short sentence wrapping up that names Open Indian Voice, like \
"That's your brief for today, from all of us at Open Indian Voice." At most \
about 120 characters.

Aim for a whole script of about 2,700 characters (intro, all spoken items and \
closing together)."""


def _user_content(stories: list[BriefStory]) -> str:
    lines = ["Yesterday's stories, in order:"]
    for s in stories:
        lines.append(f"\ncluster_id: {s.cluster_id}")
        lines.append(f"category: {s.category}; outlets covering it yesterday: {s.source_count}")
        lines.append(f"headline: {s.headline}")
        for title in s.titles:
            if title != s.headline:
                lines.append(f"- {title}")
    return "\n".join(lines)


_SPOKEN_FORBIDDEN = re.compile(r"[\d\[\]()*#;:]|\.\.|—")


def finalize_script(raw: object, stories: list[BriefStory]) -> Optional[dict]:
    """Validate Claude's JSON against the stories we sent and stamp it with
    the version timeline_audio's voicing expects. None on any mismatch: the
    audio depends on cluster ids lining up one-to-one with the offsets the app
    highlights, and on the spoken text being free of digits/markup the voice
    would read badly."""
    if not isinstance(raw, dict):
        return None
    intro, closing, items = raw.get("intro"), raw.get("closing"), raw.get("items")
    if not isinstance(intro, str) or not intro.strip() or not isinstance(closing, str) or not closing.strip():
        return None
    if not isinstance(items, list) or len(items) != len(stories):
        return None

    out_items = []
    for story, item in zip(stories, items):
        if not isinstance(item, dict) or item.get("cluster_id") != story.cluster_id:
            return None
        summary, spoken = item.get("summary"), item.get("spoken")
        if not isinstance(summary, str) or not summary.strip() or not isinstance(spoken, str) or not spoken.strip():
            return None
        out_items.append({"cluster_id": story.cluster_id, "summary": summary.strip(), "spoken": spoken.strip()})

    script = {"version": SCRIPT_VERSION, "intro": intro.strip(), "items": out_items, "closing": closing.strip()}
    for text in [script["intro"], script["closing"]] + [i["spoken"] for i in out_items]:
        if _SPOKEN_FORBIDDEN.search(text):
            logger.warning("daily brief script rejected: spoken text has digits/markup: %r", text[:120])
            return None
    return script


async def write_script(stories: list[BriefStory]) -> Optional[dict]:
    """The validated script for these stories, or None once attempts run out."""
    if not stories:
        return None
    fillers = max(2, round(TARGET_CHARS / 650))
    system = SYSTEM_PROMPT.replace("<<FILLERS>>", str(fillers))
    user = _user_content(stories)
    for attempt in range(ATTEMPTS):
        raw = await call_claude_json(
            system, user,
            model=MODEL, max_tokens=6000, temperature=None, effort="medium", timeout=180,
        )
        script = finalize_script(raw, stories)
        if script is not None:
            return script
        logger.warning("daily brief script attempt %s invalid", attempt + 1)
    return None


def build_chunks(script: dict) -> tuple[list[str], int]:
    """(chunks, intro_chars) for timeline_audio.render_and_upload: one chunk
    per story, the intro spoken with the first and the closing plus sign-off
    with the last — the same shape as timeline_audio.build_chunks, so the
    voice and silences behave identically."""
    intro = script["intro"].strip()
    chunks = [i["spoken"].strip() for i in script["items"]]
    chunks[0] = f"{intro} {chunks[0]}"
    chunks[-1] = " ".join(part for part in (chunks[-1], script["closing"].strip(), SIGN_OFF) if part)
    return chunks, len(intro) + 1
