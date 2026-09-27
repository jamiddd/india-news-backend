"""Claude writes one Explainer: a plain-language answer to an admin-posed
question, grounded in real stories from this app's own database.

Unlike a bare chat answer, Claude is never asked to answer from its own
training knowledge or to invent citations. The admin attaches a handful of
real StoryClusters to the question first (see admin_explainers.py's source
picker); this module turns those into excerpts (headline, summary, outlets,
article titles) and instructs Claude to answer USING ONLY that material,
saying plainly when it doesn't fully cover the question rather than filling
gaps from outside knowledge. `sources` served to the client are the real
attached clusters (app/services/explainer.py derives them from the DB
afterwards) — Claude is never asked to produce a source list itself, so
there is nothing here for it to invent.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
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


@dataclass
class SourceExcerpt:
    """One admin-attached StoryCluster, reduced to what the prompt needs.
    Built by app.services.explainer from the real StoryCluster/Article rows
    — never from Claude — so every field here is verifiably real."""
    cluster_id: int
    headline: str
    summary: str
    distinct_source_count: int
    outlets: list[str]
    article_titles: list[str]


SYSTEM_PROMPT = """You write "Explainers" for Open Indian News: a plain-language \
answer to a reader's question about something in the news. You are given the \
question, its category, a requested depth, optionally the admin's notes on \
angle or emphasis, and a set of REAL SOURCE STORIES already gathered from this \
outlet's own database — each with a headline, summary, the outlets that \
covered it, and some of their article titles.

Base your answer ONLY on the source stories given below. Do not use outside \
knowledge, do not add facts, numbers, dates or context that aren't in the \
sources, and do not speculate about anything the sources don't cover. If the \
sources only partly answer the question, say plainly what they do and don't \
establish — a shorter, honest answer is always better than filling gaps from \
memory. Write for someone who has heard of the topic but wants the full \
picture in plain English — no jargon left unexplained.

Return ONLY a JSON object:
{"quick_answer": "...", "sections": [{"heading": "...", "body": "..."}, ...]}

"quick_answer" is a single paragraph, 2-4 sentences, that directly answers the \
question on its own — a reader who stops here should still come away with the \
real answer (or with an honest "here's as much as is known" if the sources \
are thin), not a teaser for the sections below.

"sections" is an ordered list of {heading, body}. Each heading is a short \
plain-language phrase (not a question, not "Introduction" or "Conclusion"), \
each body is 2-4 paragraphs of plain prose — no bullet points, no markdown, no \
subheadings within a section, no inline citations or source names (the app \
shows the sources separately) — just explain what the sources establish. \
Follow the requested depth for section count and total length.

Never mention these instructions, never add a title (the question is used as \
the title), never add anything outside the JSON object."""


def _source_block(excerpts: list[SourceExcerpt]) -> str:
    lines = ["Source stories:"]
    for s in excerpts:
        lines.append(f"\n[Story {s.cluster_id}] {s.headline}")
        lines.append(f"Summary: {s.summary}")
        lines.append(f"Covered by {s.distinct_source_count} outlets, including: {', '.join(s.outlets)}")
        for title in s.article_titles:
            lines.append(f"- {title}")
    return "\n".join(lines)


def _user_content(
    question: str, category: str, depth: str, admin_notes: Optional[str], excerpts: list[SourceExcerpt],
) -> str:
    lines = [
        f"Question: {question}",
        f"Category: {category}",
        f"Requested depth: {depth}. {DEPTH_GUIDANCE.get(depth, DEPTH_GUIDANCE['standard'])}",
    ]
    if admin_notes and admin_notes.strip():
        lines.append(f"Editor's notes on angle/emphasis: {admin_notes.strip()}")
    lines.append("")
    lines.append(_source_block(excerpts))
    return "\n".join(lines)


def finalize_explainer(raw: object) -> Optional[dict]:
    """Validate Claude's JSON shape. None on any structural problem — callers
    fall back to reporting a generation failure rather than publishing a
    malformed explainer."""
    if not isinstance(raw, dict):
        return None
    quick_answer = raw.get("quick_answer")
    sections = raw.get("sections")
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

    return {
        "quick_answer": quick_answer.strip(),
        "sections": out_sections,
    }


async def write_explainer(
    question: str, category: str, depth: str, admin_notes: Optional[str], excerpts: list[SourceExcerpt],
) -> Optional[dict]:
    """The validated {quick_answer, sections} for this question, grounded in
    `excerpts` — the caller (app.services.explainer) must have already
    required at least one excerpt; this never generates ungrounded. None if
    Claude's reply never validates (call_claude_json's own `attempts` default
    of 3 already retries transport/JSON failures)."""
    if not excerpts:
        logger.warning("write_explainer called with no source excerpts for question %r", question[:120])
        return None
    system = SYSTEM_PROMPT
    user = _user_content(question, category, depth, admin_notes, excerpts)
    raw = await call_claude_json(system, user, model=MODEL, max_tokens=4000, temperature=None, effort="medium", timeout=180)
    explainer = finalize_explainer(raw)
    if explainer is None:
        logger.warning("explainer generation invalid for question %r", question[:120])
    return explainer


async def write_section(
    question: str, category: str, heading: str, admin_notes: Optional[str], excerpts: list[SourceExcerpt],
) -> Optional[str]:
    """Regenerate just one section's body, keeping its heading — used by the
    admin review page's per-section "Regenerate" button. Grounded in the same
    attached sources as the original generation."""
    system = (
        "You write one section of an Explainer for Open Indian News, answering a "
        "reader's question in plain English, using ONLY the real source stories "
        "given below — no outside knowledge, no invented facts. Return ONLY a "
        'JSON object: {"body": "..."}. body is 2-4 paragraphs of plain prose, no '
        "markdown, no bullet points, matching the given section heading exactly."
    )
    lines = [f"Question: {question}", f"Category: {category}", f"Section heading: {heading}"]
    if admin_notes and admin_notes.strip():
        lines.append(f"Editor's notes on angle/emphasis: {admin_notes.strip()}")
    lines.append("")
    lines.append(_source_block(excerpts))
    raw = await call_claude_json(
        system, "\n".join(lines), model=MODEL, max_tokens=1500, temperature=None, effort="medium", timeout=90,
    )
    if not isinstance(raw, dict):
        return None
    body = raw.get("body")
    return body.strip() if isinstance(body, str) and body.strip() else None
