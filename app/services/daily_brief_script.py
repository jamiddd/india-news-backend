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
ATTEMPTS = 3
# About one "uhm"/"uh" per 650 characters, same as the timeline scripts.
TARGET_CHARS = 2700

# Night sign-off for the Wrap-up, spoken last in place of SIGN_OFF ("have a
# nice day", wrong once it's actually night) — see build_chunks.
NIGHT_SIGN_OFF = "Thank you and good night."

# The parts of SYSTEM_PROMPT that differ between the morning Daily Brief and
# the Late-Night Wrap-up. Everything else (JSON shape, spoken-style rules,
# filler/number/acronym rules) is identical — same host, same voice, same
# podcast — so only these fill-ins change per kind.
_KIND_WORDING = {
    "brief": dict(
        title='"Your Daily Brief", the morning news rundown',
        source_desc="yesterday's top stories",
        time_word="yesterday",
        intro_example='"Good morning, and welcome to your Daily Brief on Open Indian '
                      'Voice!", then one sentence saying what is coming ("Here\'s what mattered yesterday.")',
        closing_example='"That\'s your brief for today, from all of us at Open Indian Voice."',
        user_header="Yesterday's stories, in order:",
    ),
    "wrapup": dict(
        title='"Your Late-Night Wrap-up", the evening news rundown',
        source_desc="today's top stories so far",
        time_word="today",
        intro_example='"Good evening, and welcome to your Late-Night Wrap-up on Open Indian '
                      'Voice!", then one sentence saying what is coming ("Here\'s what happened today.")',
        closing_example='"That\'s your wrap-up for tonight, from all of us at Open Indian Voice."',
        user_header="Today's stories so far, in order:",
    ),
}


def _system_prompt(kind: str) -> str:
    w = _KIND_WORDING[kind]
    return f"""You write {w['title']} of a \
podcast called "Open Indian Voice". You are given {w['source_desc']}, each \
with a headline and the titles of some articles about it. That is ALL you know: \
never add a fact, name, number, cause, reaction or year that is not in the \
headlines or titles. When the headlines are thin, say less, do not fill in.

Return ONLY a JSON object:
{{"intro": "...", "items": [{{"cluster_id": <int>, "summary": "...", "spoken": "..."}}, ...], "closing": "..."}}
"items" must contain EVERY story you were given, in the SAME order, with its \
cluster_id copied exactly.

"summary" is for the screen: one plain sentence of about twenty words, no more \
than twenty-five, past tense, neutral, no markdown. Ordinary digits are fine.

"spoken" is read aloud by a single fixed male Indian-English text-to-speech \
voice that reads EXACTLY the words you write. About thirty-five to forty-five \
words per story, two or three sentences. Rules:
- Casual, warm host in the present tense ("Parliament passes...", "the R B I \
  holds rates..."). Say "{w['time_word']}" if a time is needed; never name a date or a \
  year. Contractions and plain words. No interjections ("Wow", "Oh my God"), \
  never "phew", no all-caps words.
- Each sentence has at most two parts. Commas only at natural hinge points. No \
  semicolons, em dashes, colons, parentheses, bullet points or markdown.
- Start each story with a very short spoken transition that varies ("First up,", \
  "Meanwhile,", "In business,", "On the sports front,", "And in tech,"). The \
  first story needs no transition beyond the intro.
- Write EVERY number, amount and percentage in words ("twelve thousand", "two \
  lakh crore rupees"), with no digits at all. Indian units for rupees. This \
  includes a numeral inside a title or a name: write it the way it is said \
  ("Drishyam three", "iPhone seventeen", "Article three seventy").
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
the day, like {w['intro_example']}. At most about 220 characters.

"closing": one short sentence wrapping up that names Open Indian Voice, like \
{w['closing_example']} At most about 120 characters.

Aim for a whole script of about 2,700 characters (intro, all spoken items and \
closing together)."""


def _user_content(stories: list[BriefStory], kind: str = "brief") -> str:
    lines = [_KIND_WORDING[kind]["user_header"]]
    for s in stories:
        lines.append(f"\ncluster_id: {s.cluster_id}")
        lines.append(f"category: {s.category}; outlets covering it yesterday: {s.source_count}")
        lines.append(f"headline: {s.headline}")
        for title in s.titles:
            if title != s.headline:
                lines.append(f"- {title}")
    return "\n".join(lines)


_SPOKEN_FORBIDDEN = re.compile(r"[\d\[\]()*#;:]|\.\.|—")


def finalize_script(
    raw: object, stories: list[BriefStory], problems: Optional[list[str]] = None
) -> Optional[dict]:
    """Validate Claude's JSON against the stories we sent and stamp it with
    the version timeline_audio's voicing expects. None on any mismatch: the
    audio depends on cluster ids lining up one-to-one with the offsets the app
    highlights, and on the spoken text being free of digits/markup the voice
    would read badly. problems, when given, collects the offending spoken text
    so write_script can tell the next attempt what to repair."""
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
    spoken_texts = [script["intro"], script["closing"]] + [i["spoken"] for i in out_items]
    bad = [text for text in spoken_texts if _SPOKEN_FORBIDDEN.search(text)]
    if bad:
        for text in bad:
            logger.warning("daily brief script rejected: spoken text has digits/markup: %r", text[:120])
        if problems is not None:
            problems.extend(text.strip() for text in bad)
        return None
    return script


def _retry_note(problems: list[str]) -> str:
    """Appended to the user message after a rejected attempt: the exact text
    that failed, so the next attempt repairs it instead of writing the same
    thing again. A numeral inside a film or product name is the usual cause,
    and without this note the retry sees an identical prompt."""
    if not problems:
        return (
            "\n\nYour last attempt was rejected: it did not match the JSON shape, the "
            "cluster ids and their order, or the length rules above. Follow them exactly."
        )
    quoted = "\n".join(f'- "{p[:200]}"' for p in problems[:4])
    return (
        "\n\nYour last attempt was rejected. This spoken text contains a digit or "
        f"punctuation the voice cannot read:\n{quoted}\n"
        "Write it again with every numeral spelled out in words, including a numeral "
        'that is part of a title or a name ("Drishyam three", "iPhone seventeen"), and '
        "with no brackets, parentheses, asterisks, hashes, semicolons, colons, double "
        "dots or em dashes. Keep the rest of the script as it was."
    )


async def write_script(stories: list[BriefStory], kind: str = "brief") -> Optional[dict]:
    """The validated script for these stories, or None once attempts run out.
    kind picks the wording (see _KIND_WORDING): 'brief' or 'wrapup'. Each retry
    is told what the last attempt got wrong (see _retry_note)."""
    if not stories:
        return None
    fillers = max(2, round(TARGET_CHARS / 650))
    system = _system_prompt(kind).replace("<<FILLERS>>", str(fillers))
    user = _user_content(stories, kind)
    note = ""
    for attempt in range(ATTEMPTS):
        raw = await call_claude_json(
            system, user + note,
            model=MODEL, max_tokens=6000, temperature=None, effort="medium", timeout=180,
        )
        problems: list[str] = []
        script = finalize_script(raw, stories, problems)
        if script is not None:
            return script
        logger.warning("daily brief script attempt %s invalid", attempt + 1)
        note = _retry_note(problems)
    return None


def build_chunks(script: dict, kind: str = "brief") -> tuple[list[str], int]:
    """(chunks, intro_chars) for timeline_audio.render_and_upload: one chunk
    per story, the intro spoken with the first and the closing plus sign-off
    with the last — the same shape as timeline_audio.build_chunks, so the
    voice and silences behave identically. kind picks the sign-off: SIGN_OFF
    ("have a nice day") for the morning Brief, NIGHT_SIGN_OFF for the Wrap-up."""
    intro = script["intro"].strip()
    chunks = [i["spoken"].strip() for i in script["items"]]
    chunks[0] = f"{intro} {chunks[0]}"
    sign_off = NIGHT_SIGN_OFF if kind == "wrapup" else SIGN_OFF
    chunks[-1] = " ".join(part for part in (chunks[-1], script["closing"].strip(), sign_off) if part)
    return chunks, len(intro) + 1
