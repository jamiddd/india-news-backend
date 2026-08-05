import re
from typing import Optional

# Once any of these phrases turns up (case-insensitive, anywhere in a line),
# that line and everything after it is discarded. Each one reliably marks
# the start of a trailing boilerplate block — author bio, newsletter/social
# CTA, comments-section disclaimer — rather than article body, across the
# outlets we ingest (India Today, Hindustan Times, News18, Livemint, ...).
# Site-agnostic by design: targets recurring phrasing, not any one outlet's
# markup, so it degrades gracefully (does nothing) on sources it wasn't
# tuned against instead of mangling them.
_TRUNCATE_MARKERS = [
    "about the author",
    "disclaimer: comments reflect",
    "loading comments",
    "is your trusted source for breaking news",  # News18/CNN-News18 sign-off
    "subscribe now and join our community",
    "follow every breaking story live",
    "wait for it",
    "exceeded the limit to bookmark",
]

# Standalone lines that are pure site chrome wherever they occur — dropped
# outright (exact match after stripping) rather than used as a cutoff.
_DROP_LINE_EXACT = {
    "- ends",
    "advertisement",
    "✕",
    "read more",
    "this is a developing story. it will be updated.",
}

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_NON_WORD_RE = re.compile(r"\W+")


def clean_extracted_text(text: Optional[str], title: Optional[str] = None) -> Optional[str]:
    """
    Strip known publisher boilerplate out of scraped article text: author
    bios, social/subscribe CTAs, comment-section disclaimers, stray HTML
    remnants, and a leading line that just repeats the headline (already
    stored separately as Article.title).

    Returns None if cleaning leaves nothing usable, so callers fall back to
    the RSS snippet the same way a failed scrape would.
    """
    if not text:
        return None

    text = _HTML_TAG_RE.sub(" ", text)
    lines = [ln.strip() for ln in text.splitlines()]

    if title and lines:
        norm_title = _NON_WORD_RE.sub(" ", title).strip().lower()
        norm_first = _NON_WORD_RE.sub(" ", lines[0]).strip().lower()
        if norm_title and norm_first == norm_title:
            lines = lines[1:]

    cleaned = []
    for line in lines:
        if not line:
            continue
        lowered = line.lower()
        if lowered in _DROP_LINE_EXACT:
            continue
        if any(marker in lowered for marker in _TRUNCATE_MARKERS):
            break
        cleaned.append(line)

    result = "\n".join(cleaned).strip()
    return result or None
