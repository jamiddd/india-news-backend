import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Optional

import trafilatura
from curl_cffi.requests import AsyncSession

from app.services.content_cleaner import clean_extracted_text

logger = logging.getLogger(__name__)

EXTRACT_TIMEOUT_SECONDS = 10.0
# Impersonate a real Chrome TLS fingerprint — several sources (Gadgets360,
# Indian Express) 403 a plain httpx/curl request at the WAF layer (Akamai
# etc.) no matter what headers are sent, because the block is keyed off the
# TLS handshake itself, not anything in the HTTP request.
IMPERSONATE = "chrome124"

# Matches a <meta property="og:image" content="..."> tag regardless of
# attribute order (some sites emit content= before property=).
_OG_IMAGE_RE = re.compile(
    r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', re.IGNORECASE
)
_OG_IMAGE_RE_ALT = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', re.IGNORECASE
)
# og:video / og:video:url / og:video:secure_url — same attribute-order
# ambiguity as og:image, so match both orderings and both property names.
_OG_VIDEO_RE = re.compile(
    r'<meta[^>]+property=["\']og:video(?::(?:url|secure_url))?["\'][^>]+content=["\']([^"\']+)["\']', re.IGNORECASE
)
_OG_VIDEO_RE_ALT = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:video(?::(?:url|secure_url))?["\']', re.IGNORECASE
)


@dataclass
class ExtractedArticle:
    content: Optional[str]
    og_image_url: Optional[str]
    og_video_url: Optional[str] = None


def _extract_og_image(html: str) -> Optional[str]:
    match = _OG_IMAGE_RE.search(html) or _OG_IMAGE_RE_ALT.search(html)
    return match.group(1) if match else None


def _extract_og_video(html: str) -> Optional[str]:
    match = _OG_VIDEO_RE.search(html) or _OG_VIDEO_RE_ALT.search(html)
    return match.group(1) if match else None


async def extract_full_content(client: AsyncSession, url: str, title: Optional[str] = None) -> ExtractedArticle:
    """
    Fetch the article page at `url` and pull out:
    - the main body text (byline/nav/ads/comments stripped) via trafilatura,
      then cleaned of recurring publisher boilerplate (author bios,
      social/subscribe CTAs, comment disclaimers) via clean_extracted_text()
    - its og:image meta tag, as a fallback image source for feeds that don't
      carry one of their own (see image_extractor.extract_rss_image, which
      callers should try first)

    Returns ExtractedArticle(None, None) on any failure (network error,
    non-200, no extractable text) so callers can fall back to the RSS
    snippet/no-image the same way a failed scrape would.
    """
    try:
        response = await client.get(url, timeout=EXTRACT_TIMEOUT_SECONDS, allow_redirects=True, impersonate=IMPERSONATE)
        if response.status_code != 200 or not response.text:
            return ExtractedArticle(None, None)

        # trafilatura's extract() is a synchronous CPU-bound parse (lxml) —
        # run it off the event loop so a big/slow page can't stall polling.
        text = await asyncio.to_thread(
            trafilatura.extract,
            response.text,
            include_comments=False,
            include_tables=False,
            favor_precision=True,  # prioritize cutting boilerplate over recall of every last line
            deduplicate=True,
        )
        content = clean_extracted_text(text, title)
        og_image_url = _extract_og_image(response.text)
        og_video_url = _extract_og_video(response.text)
        return ExtractedArticle(content, og_image_url, og_video_url)
    except Exception as e:
        logger.debug(f"Full-content extraction failed for {url}: {e}")
        return ExtractedArticle(None, None)
