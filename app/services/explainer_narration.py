"""Claude converts an already-written Explainer answer into a spoken script
for text-to-speech — someone genuinely explaining the question, not a
verbatim reading of the on-screen quick_answer/sections. Reuses the
tone/sentence-flow rules tuned by ear for Timeline and Daily Brief narration
(timeline_narrative.SPOKEN_SCRIPT_RULES), with a framing suited to a
stand-alone answer rather than a recurring show episode.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.services.llm_gen import call_claude_json
from app.services.timeline_narrative import SPOKEN_SCRIPT_RULES, finalize_spoken_script

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-5"

EXPLAINER_SPOKEN_SCRIPT_SYSTEM_PROMPT = """You are converting an already-written "explainer" answer \
into a SPOKEN script for text-to-speech — a real person genuinely explaining WHY \
or HOW something happened, not reading the written answer aloud.

You'll be given the reader's question and the already-written, already-published \
answer: a "quick_answer" paragraph and an ordered list of section {heading, body} \
pairs. Do not change their facts, order, or count, and do not add anything \
beyond what they establish.

<<SPOKEN_SCRIPT_RULES>>

This is a stand-alone answer to one question, not an episode of a recurring \
show, so adjust the shape above:
- "intro": do NOT use a "welcome back" greeting. Name Open Indian Voice once, \
  briefly, then go straight into explaining, the way a knowledgeable friend \
  answers on the spot: "Hey, this is Open Indian Voice. So, why did..." (a \
  close variant is fine). Give the real answer here, in your own words — the \
  same substance as quick_answer, not a teaser for what follows.
- "beats": one per section, in the same order. Each beat explains that part \
  of the reasoning the way a person actually reasons out loud, connected to \
  what was just said ("that's what set this off", "and that's where it gets \
  interesting") — not a flat restatement of the section body, and never read \
  the heading aloud.
- "closing": one to two sentences leaving the listener with the real \
  takeaway, ending with a short line naming Open Indian Voice.

Respond with ONLY a JSON object, no markdown fences, matching exactly:
{
  "intro": "spoken version of the quick answer, in the host's own words",
  "beats": ["spoken text for section 0", "spoken text for section 1", ...],
  "closing": "one to two sentence spoken wrap-up naming Open Indian Voice"
}"""
EXPLAINER_SPOKEN_SCRIPT_SYSTEM_PROMPT = EXPLAINER_SPOKEN_SCRIPT_SYSTEM_PROMPT.replace(
    "<<SPOKEN_SCRIPT_RULES>>", SPOKEN_SCRIPT_RULES
)


def _filler_target(quick_answer: str, section_bodies: list[str]) -> int:
    """Same formula as timeline_narrative._filler_target: about one filler
    per 650 characters of the finished script, which runs roughly 20% longer
    than the written text it is converted from."""
    written = len(quick_answer) + sum(len(b) for b in section_bodies)
    return max(1, round(written * 1.2 / 650))


def _user_content(question: str, quick_answer: str, sections: list[dict]) -> str:
    section_bodies = [s["body"] for s in sections]
    lines = [f"Question: {question}", "", f"quick_answer: {quick_answer}", "", "sections:"]
    for i, s in enumerate(sections):
        lines.append(f"\n{i + 1}. heading: {s['heading']}\nbody: {s['body']}")
    lines.append(f"\nTarget filler count for the whole script: {_filler_target(quick_answer, section_bodies)}.")
    return "\n".join(lines)


async def write_explainer_spoken_script(
    question: str, quick_answer: str, sections: list[dict], *, attempts: int = 2
) -> Optional[dict]:
    """The validated spoken_script for one explainer's narration, with the
    same {intro, beats, closing, version} shape timeline_audio.build_chunks
    expects — one beat per section. None if Claude never returns a script
    whose beat count matches `sections` (call_claude_json's own internal
    retries already cover transport/JSON failures; this loop is only for a
    shape mismatch). Callers should fall back to reading the written text
    verbatim rather than failing generation outright."""
    if not sections:
        return None
    system = EXPLAINER_SPOKEN_SCRIPT_SYSTEM_PROMPT
    user = _user_content(question, quick_answer, sections)
    for attempt in range(attempts):
        raw = await call_claude_json(
            system, user, model=MODEL, max_tokens=6000, temperature=None, effort="medium", timeout=90,
        )
        script = finalize_spoken_script(raw, len(sections))
        if script is not None:
            return script
        logger.warning(
            "explainer spoken script attempt %d/%d invalid for question %r", attempt + 1, attempts, question[:120]
        )
    return None
