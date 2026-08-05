import re
from typing import Any, Optional

_IMG_TAG_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)


def extract_rss_image(entry: Any) -> Optional[str]:
    """
    Pull an image URL straight out of a feedparser entry, in order of how
    reliable/common each source is across our feeds:

    1. Media RSS <media:content> (The Hindu, HT, NDTV, News18, Livemint)
    2. Media RSS <media:thumbnail> (some feeds use this instead)
    3. An <enclosure> link of an image type (Times of India)
    4. A stray <img src="..."> embedded in the summary/description HTML
       (India Today doesn't tag images at all, but embeds one in the snippet)

    Returns None if none of these are present — callers should fall back to
    scraping the article page's og:image (see extractor.py).
    """
    media_content = getattr(entry, "media_content", None)
    if media_content and isinstance(media_content, list):
        url = media_content[0].get("url")
        if url:
            return url

    media_thumbnail = getattr(entry, "media_thumbnail", None)
    if media_thumbnail and isinstance(media_thumbnail, list):
        url = media_thumbnail[0].get("url")
        if url:
            return url

    for link in getattr(entry, "links", None) or []:
        if link.get("rel") == "enclosure" and str(link.get("type", "")).startswith("image"):
            href = link.get("href")
            if href:
                return href

    summary = getattr(entry, "summary", "") or getattr(entry, "description", "")
    if summary:
        match = _IMG_TAG_RE.search(summary)
        if match:
            return match.group(1)

    return None
