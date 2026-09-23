import io
import re
import logging
from typing import Any, Optional, TYPE_CHECKING
from urllib.parse import urlparse, unquote
from PIL import Image
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

# How many leading bytes of a candidate image to pull via a Range request in
# fetch_image_dimensions — enough header room for a JPEG's SOF marker to
# show up even behind a large EXIF/thumbnail block (which maxes out at 64KB
# per the JPEG spec), while PNG/WebP/GIF need only their first few dozen
# bytes. PIL.Image.open() parses the header lazily and never decodes pixel
# data unless .load() is called, so this stays cheap even when a server
# ignores the Range header and returns the whole file.
_DIMENSION_FETCH_RANGE_BYTES = 65536

# The shorter of "HD" thresholds in common use (720p) — a photo whose longer
# edge meets this is treated as HD regardless of orientation.
HD_MIN_LONG_EDGE_PX = 1280


def is_hd_image(width: Optional[int], height: Optional[int]) -> bool:
    """True if a photo of these pixel dimensions counts as HD. Missing
    dimensions (fetch failed, or a legacy row predating image_width/height)
    are NOT HD by this check, but callers ranking candidates should still
    treat "unknown" and "known non-HD" as separate buckets — see
    main.py's _cluster_to_list_out image_priority_sort — since a legacy
    photo with no recorded size is not necessarily low quality."""
    return width is not None and height is not None and max(width, height) >= HD_MIN_LONG_EDGE_PX


async def fetch_image_dimensions(
    client: "CurlAsyncSession", image_url: Optional[str]
) -> tuple[Optional[int], Optional[int]]:
    """Best-effort (width, height) for a candidate lead image, read from
    just the first _DIMENSION_FETCH_RANGE_BYTES bytes via a Range request —
    most image CDNs honor it, so this is far cheaper than downloading the
    whole photo just to learn its size.

    Fails open (returns (None, None), i.e. "quality unknown") on any error,
    timeout, non-2xx status, or an image PIL can't parse from a partial
    read — this is a ranking signal, not a correctness dependency, and a
    real photo should never be dropped over a flaky fetch or a truncated
    header."""
    if not image_url:
        return None, None
    try:
        response = await client.get(
            image_url,
            timeout=BROKEN_IMAGE_CHECK_TIMEOUT_SECONDS,
            headers={"Range": f"bytes=0-{_DIMENSION_FETCH_RANGE_BYTES - 1}"},
            impersonate=IMPERSONATE,
        )
        if response.status_code >= 400:
            return None, None
        with Image.open(io.BytesIO(response.content)) as img:
            width, height = img.size
            return width, height
    except Exception:
        return None, None

_IMG_TAG_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)

# Filename tokens CDNs use to mark a resized/cropped variant of the same
# photo (e.g. Morung Express's RSS <enclosure> points at a "..._thumb_..."
# filename while the scraped page's og:image points at the same photo's
# full-size filename). Stripped before comparing so the two don't get kept
# as if they were different photos.
_SIZE_VARIANT_MARKERS = ("thumb", "thumbnail", "small", "medium", "mini", "preview", "scaled", "resized")
_DIMENSION_RE = re.compile(r"\d{2,4}x\d{2,4}")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]")


def _image_dedupe_key(image_url: str) -> str:
    """Reduces an image URL to just its filename's alphanumeric characters,
    with size/thumbnail markers and WxH dimension tags stripped, so that
    e.g. '.../80521331_1789222770_thumb_21499369_1789222770_Workshop.jpg'
    and '.../21499369_1789222770_Workshop.jpg' both collapse to a key where
    one is a substring of the other (the thumb variant's filename embeds the
    full-size one's, prefixed by its own id/timestamp — observed live on
    Morung Express feeds)."""
    basename = urlparse(image_url).path.lower().rsplit("/", 1)[-1]
    name = basename.rsplit(".", 1)[0] or basename
    name = _DIMENSION_RE.sub("", name)
    for marker in _SIZE_VARIANT_MARKERS:
        name = name.replace(marker, "")
    return _NON_ALNUM_RE.sub("", name)


def is_same_image_url(url_a: str, url_b: str) -> bool:
    """True if url_a and url_b are the literal same URL modulo percent-encoding
    differences — e.g. RSS gives a raw Unicode character (a smart quote) in
    the path where the scraped page's og:image percent-encodes the same
    character. Plain string equality misses this; is_same_photo_different_size
    below is for genuinely different filenames/CDNs serving the same photo."""
    return unquote(url_a) == unquote(url_b)


def is_same_photo_different_size(url_a: str, url_b: str) -> bool:
    """True if url_a and url_b look like the same photo at a different
    resolution/crop rather than two distinct photos — see _image_dedupe_key.
    Exact duplicate URLs are handled separately by simple string equality;
    this only needs to catch same-photo-different-filename."""
    key_a, key_b = _image_dedupe_key(url_a), _image_dedupe_key(url_b)
    if not key_a or not key_b:
        return False
    return key_a == key_b or key_a in key_b or key_b in key_a

# How many *distinct* articles from the same source may reuse the exact same
# image URL before we conclude it isn't a real per-story photo but a
# publisher-wide default/logo/placeholder (e.g. The Hindu falls back to a
# generic section thumbnail on some feeds when a story has no dedicated
# image). Kept low because a legitimate photo being reused 3+ times across
# unrelated stories from one outlet is rare.
PLACEHOLDER_REUSE_THRESHOLD = 3

# India Today's World section has no licensed photo for most wire-agency
# pickups, so instead of a real per-story image its CMS falls back to a
# branded "PTI: International" template card — a globe render, a flag
# collage, a world-leaders composite, etc, always overlaid with that same
# "INDIA TODAY / PTI: International" badge and always named like
# "pti-international-2-original_284-sixteen_nine.png". Confirmed live
# 2026-09-22: 14 of 21 India Today World RSS items on one poll used this
# template, and the scraped article page's own og:image is the identical
# templated URL, so there's no better fallback to prefer over dropping it.
# The filename's own id increments on every upload, so
# PLACEHOLDER_REUSE_THRESHOLD's exact-URL-reuse check never fires (no two
# articles share a literal URL) — this catches it by name instead.
_GENERIC_CARD_FILENAME_RE = re.compile(r"^pti-international-\d+-original_\d+-sixteen_nine$", re.IGNORECASE)


def is_generic_branded_placeholder(image_url: Optional[str]) -> bool:
    """True for India Today's "PTI: International" templated placeholder
    card (see _GENERIC_CARD_FILENAME_RE) — a real-looking but non-story
    image that a frequency-based check can't catch because its URL is
    never exactly reused."""
    if not image_url:
        return False
    basename = urlparse(image_url).path.rsplit("/", 1)[-1]
    name = basename.rsplit(".", 1)[0]
    return bool(_GENERIC_CARD_FILENAME_RE.match(name))


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
