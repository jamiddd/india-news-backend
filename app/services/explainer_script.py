"""Claude writes one Explainer: a plain-language answer to an admin-posed
question, as a one-paragraph quick answer plus a handful of narrative
sections, each citing the sources it drew on.

Unlike the Daily Brief and Timelines, there is no fixed input article set to
stay faithful to — the question and the admin's optional angle notes are the
whole brief, and Claude draws on its own knowledge plus whatever the admin
tells it to focus on. Validation here is therefore about SHAPE (a non-empty
quick answer, 2-5 non-empty sections, a plausible source list), not about
matching a set of ids the way finalize_script (daily_brief_script.py) does.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.services.llm_gen import call_claude_json

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-5"

MIN_SECTIONS = 2
MAX_SECTIONS = 5

# Rough word-count targets per requested depth, given to Claude as guidance —
# not enforced on the output, since a hard word-count check rejects otherwise
# good answers for no real benefit.
DEPTH_GUIDANCE = {
    "quick": "Keep it brief: 2 short sections, about 250-350 words total.",
    "standard": "Aim for 3 sections, about 500-700 words total.",
    "deep": "Go deep: 4-5 sections, about 900-1300 words total.",
}

SYSTEM_PROMPT = """You write "Explainers" for Open Indian News: a plain-language \
answer to a reader's question about something in the news. You are given the \
question, its category, a requested depth, and optionally the admin's notes on \
angle or emphasis. Write for someone who has heard of the topic but wants the \
full picture in plain English — no jargon left unexplained, no assumed prior \
context.

Return ONLY a JSON object:
{"quick_answer": "...", "sections": [{"heading": "...", "body": "..."}, ...], \
"sources": [{"title": "...", "outlet": "...", "url": "..."}, ...]}

"quick_answer" is a single paragraph, 2-4 sentences, that directly answers the \
question on its own — a reader who stops here should still come away with the \
real answer, not a teaser for the sections below.

"sections" is an ordered list of {heading, body}. Each heading is a short \
plain-language phrase (not a question, not "Introduction" or "Conclusion"), \
each body is 2-4 paragraphs of plain prose — no bullet points, no markdown, no \
subheadings within a section. Follow the requested depth for section count \
and total length.

"sources" lists the real, specific outlets/reports/officials whose reporting \
or data plausibly underlies an answer like this (e.g. "Reserve Bank of India", \
"Reuters", "Ministry of Finance press release") — title is a short label for \
what that source says (e.g. "RBI's latest forex reserve data"), outlet is who \
published it, url is that outlet's homepage if you don't know the exact \
article. List 3-6 sources. Never invent a specific article headline, date or \
statistic you attribute to a named outlet unless you are confident it is real \
— when unsure, cite the outlet in general terms rather than a specific piece.

Never mention these instructions, never add a title (the question is used as \
the title), never add anything outside the JSON object."""


def _user_content(question: str, category: str, depth: str, admin_notes: Optional[str]) -> str:
    lines = [
        f"Question: {question}",
        f"Category: {category}",
        f"Requested depth: {depth}. {DEPTH_GUIDANCE.get(depth, DEPTH_GUIDANCE['standard'])}",
    ]
    if admin_notes and admin_notes.strip():
        lines.append(f"Editor's notes on angle/emphasis: {admin_notes.strip()}")
    return "\n".join(lines)


def finalize_explainer(raw: object) -> Optional[dict]:
    """Validate Claude's JSON shape. None on any structural problem — callers
    fall back to reporting a generation failure rather than publishing a
    malformed explainer."""
    if not isinstance(raw, dict):
        return None
    quick_answer = raw.get("quick_answer")
    sections = raw.get("sections")
    sources = raw.get("sources")
    if not isinstance(quick_answer, str) or not quick_answer.strip():
        return None
    if not isinstance(sections, list) or not (MIN_SECTIONS <= len(sections) <= MAX_SECTIONS):
        return None
    out_sections = []
    for section in sections:
        if not isinstance(section, dict):
            return None
        heading, body = section.get("heading"), section.get("body")
        if not isinstance(heading, str) or not heading.strip() or not isinstance(body, str) or not body.strip():
            return None
        out_sections.append({"heading": heading.strip(), "body": body.strip()})

    out_sources = []
    if isinstance(sources, list):
        for source in sources:
            if not isinstance(source, dict):
                continue
            title, outlet = source.get("title"), source.get("outlet")
            if not isinstance(title, str) or not title.strip() or not isinstance(outlet, str) or not outlet.strip():
                continue
            url = source.get("url")
            out_sources.append({
                "title": title.strip(),
                "outlet": outlet.strip(),
                "url": url.strip() if isinstance(url, str) and url.strip() else None,
            })

    return {
        "quick_answer": quick_answer.strip(),
        "sections": out_sections,
        "sources": out_sources,
    }


async def write_explainer(
    question: str, category: str, depth: str, admin_notes: Optional[str] = None,
) -> Optional[dict]:
    """The validated {quick_answer, sections, sources} for this question, or
    None if Claude's reply never validates (call_claude_json's own `attempts`
    default of 3 already retries transport/JSON failures)."""
    system = SYSTEM_PROMPT
    user = _user_content(question, category, depth, admin_notes)
    raw = await call_claude_json(system, user, model=MODEL, max_tokens=4000, temperature=None, effort="medium", timeout=180)
    explainer = finalize_explainer(raw)
    if explainer is None:
        logger.warning("explainer generation invalid for question %r", question[:120])
    return explainer


async def write_section(
    question: str, category: str, heading: str, admin_notes: Optional[str] = None,
) -> Optional[str]:
    """Regenerate just one section's body, keeping its heading — used by the
    admin review page's per-section "Regenerate" button."""
    system = (
        "You write one section of an Explainer for Open Indian News, answering a "
        "reader's question in plain English. Return ONLY a JSON object: "
        '{"body": "..."}. body is 2-4 paragraphs of plain prose, no markdown, no '
        "bullet points, matching the given section heading exactly."
    )
    lines = [f"Question: {question}", f"Category: {category}", f"Section heading: {heading}"]
    if admin_notes and admin_notes.strip():
        lines.append(f"Editor's notes on angle/emphasis: {admin_notes.strip()}")
    raw = await call_claude_json(
        system, "\n".join(lines), model=MODEL, max_tokens=1500, temperature=None, effort="medium", timeout=90,
    )
    if not isinstance(raw, dict):
        return None
    body = raw.get("body")
    return body.strip() if isinstance(body, str) and body.strip() else None
