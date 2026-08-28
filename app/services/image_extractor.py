import re
import logging
from typing import Any, Optional, TYPE_CHECKING
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Article
from app.services.extractor import IMPERSONATE

if TYPE_CHECKING:
    from curl_cffi.requests import AsyncSession as CurlAsyncSession

logger = logging.getLogger(__name__)

# How long to wait on the HEAD check in is_broken_image_url before giving up
# and treating the URL as fine (fail open — see that function's docstring).
BROKEN_IMAGE_CHECK_TIMEOUT_SECONDS = 5

_IMG_TAG_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)

# How many *distinct* articles from the same source may reuse the exact same
# image URL before we conclude it isn't a real per-story photo but a
# publisher-wide default/logo/placeholder (e.g. The Hindu falls back to a
# generic section thumbnail on some feeds when a story has no dedicated
# image). Kept low because a legitimate photo being reused 3+ times across
# unrelated stories from one outlet is rare.
PLACEHOLDER_REUSE_THRESHOLD = 3


async def is_placeholder_image(session: AsyncSession, source_id: int, image_url: Optional[str], url_hash: str) -> bool:
    """Detect a per-source default/placeholder image by frequency: if a
    source has already used this exact image URL on N-or-more other
    (different-article) rows, treat it as a non-story-specific placeholder
    rather than a real lead image, so callers can drop it and show no image
    (or a fallback) instead of a repeated stock/logo thumbnail."""
    if not image_url:
        return False
    count = await session.scalar(
        select(func.count(Article.id)).where(
            Article.source_id == source_id,
            Article.image_url == image_url,
            Article.url_hash != url_hash,
        )
    )
    if count and count >= PLACEHOLDER_REUSE_THRESHOLD:
        logger.info(f"[Placeholder image detected] source_id={source_id} reused {count}x: {image_url}")
        return True
    return False


async def is_broken_image_url(client: "CurlAsyncSession", image_url: Optional[str]) -> bool:
    """HEAD-checks a candidate image/video-thumbnail URL for a genuinely
    empty (Content-Length: 0) response. Observed live: a tosshub.com
    (India Today) video-thumbnail URL returning `200 OK` with a 0-byte S3
    object — apparently a race between the publisher's own thumbnail
    pipeline finishing and their CDN going live at scrape time. Coil can't
    render 0 bytes, so without this the app just shows a blank tinted
    placeholder forever for that story.

    Fails open (returns False, i.e. "treat as fine") on any error, timeout,
    non-2xx status, or a missing/unparseable Content-Length header — this
    is a best-effort filter against one specific failure mode, not a
    correctness dependency, and a real photo should never be dropped over
    a flaky HEAD request or a CDN that doesn't report Content-Length."""
    if not image_url:
        return False
    try:
        response = await client.head(
            image_url,
            timeout=BROKEN_IMAGE_CHECK_TIMEOUT_SECONDS,
            impersonate=IMPERSONATE,
        )
        if response.status_code >= 400:
            return False
        content_length = response.headers.get("content-length")
        return content_length is not None and int(content_length) == 0
    except Exception:
        return False


def extract_rss_video(entry: Any) -> Optional[str]:
    """
    Pull a video URL straight out of a feedparser entry, mirroring
    extract_rss_image's sources but filtered to video-typed entries:

    1. Media RSS <media:content> whose type/medium is video
    2. An <enclosure> link of a video type

    Returns None if none of these are present — callers should fall back to
    scraping the article page's og:video (see extractor.py).
    """
    media_content = getattr(entry, "media_content", None)
    if media_content and isinstance(media_content, list):
        for item in media_content:
            medium = str(item.get("medium", ""))
            media_type = str(item.get("type", ""))
            if medium == "video" or media_type.startswith("video"):
                url = item.get("url")
                if url:
                    return url

    for link in getattr(entry, "links", None) or []:
        if link.get("rel") == "enclosure" and str(link.get("type", "")).startswith("video"):
            href = link.get("href")
            if href:
                return href

    return None


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
