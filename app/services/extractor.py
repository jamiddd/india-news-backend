import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Optional

import httpx
import trafilatura

from app.services.content_cleaner import clean_extracted_text

logger = logging.getLogger(__name__)

EXTRACT_TIMEOUT_SECONDS = 10.0

# Matches a <meta property="og:image" content="..."> tag regardless of
# attribute order (some sites emit content= before property=).
_OG_IMAGE_RE = re.compile(
    r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', re.IGNORECASE
)
_OG_IMAGE_RE_ALT = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', re.IGNORECASE
)


@dataclass
class ExtractedArticle:
    content: Optional[str]
    og_image_url: Optional[str]


def _extract_og_image(html: str) -> Optional[str]:
    match = _OG_IMAGE_RE.search(html) or _OG_IMAGE_RE_ALT.search(html)
    return match.group(1) if match else None


async def extract_full_content(client: httpx.AsyncClient, url: str, title: Optional[str] = None) -> ExtractedArticle:
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
        response = await client.get(url, timeout=EXTRACT_TIMEOUT_SECONDS, follow_redirects=True)
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
        return ExtractedArticle(content, og_image_url)
    except Exception as e:
        logger.debug(f"Full-content extraction failed for {url}: {e}")
        return ExtractedArticle(None, None)
