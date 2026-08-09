import html
import re
from typing import Optional


def decode_entities(text: Optional[str]) -> Optional[str]:
    """
    Unescape HTML entities (&amp;, &#39;, ...), repeating up to a few times
    since some publisher CMSes (News18, HT) double-encode their RSS titles
    (e.g. "J&amp;amp;K" in the raw feed survives feedparser's one pass of
    decoding and lands in the DB as the literal string "J&amp;K"). Stops as
    soon as a pass changes nothing, so plain text with a bare "&" is left
    alone.
    """
    if not text:
        return text
    decoded = text
    for _ in range(3):
        next_pass = html.unescape(decoded)
        if next_pass == decoded:
            break
        decoded = next_pass
    return decoded

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
    "n18oc",  # News18's internal taxonomy tags, glued right before its sign-off block
    "is your trusted source for breaking news",  # News18/CNN-News18 sign-off
    "subscribe now and join our community",
    "follow every breaking story live",
    "wait for it",
    "exceeded the limit to bookmark",
    "get your daily dose of tech news",  # Gadgets 360 sign-off
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

        # Boilerplate is sometimes glued directly onto the tail of the last
        # real sentence with no line break (e.g. a short "BREAKING:" stub
        # whose one paragraph runs straight into News18's subscribe/social
        # block) — truncate from the marker's position, not the whole line,
        # so genuine leading text on that same line survives.
        marker_pos = None
        for marker in _TRUNCATE_MARKERS:
            idx = lowered.find(marker)
            if idx != -1 and (marker_pos is None or idx < marker_pos):
                marker_pos = idx
        if marker_pos is not None:
            # If the marker sits inside a run of non-whitespace characters
            # (e.g. News18's taxonomy tags glued straight onto real prose:
            # "...safety. -newsn18oc_worldCNN-News18 is your trusted..."),
            # back up to the start of that glued token so the whole blob
            # gets dropped, not just the matched substring onward.
            cut = marker_pos
            while cut > 0 and not line[cut - 1].isspace():
                cut -= 1
            # Strip trailing bullet/dash clutter left dangling right before
            # the cut (e.g. "...paragraph text.\n- ABOUT THE AUTHOR...");
            # if nothing alphanumeric survives, the "prefix" was itself just
            # boilerplate punctuation, not real content — drop it too.
            prefix = line[:cut].strip().rstrip(" -–—•*:")
            if prefix and any(ch.isalnum() for ch in prefix):
                cleaned.append(prefix)
            break

        cleaned.append(line)

    result = "\n".join(cleaned).strip()
    return result or None
