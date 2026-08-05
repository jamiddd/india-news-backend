import asyncio
import logging
from typing import Optional

import httpx
import trafilatura

logger = logging.getLogger(__name__)

EXTRACT_TIMEOUT_SECONDS = 10.0


async def extract_full_content(client: httpx.AsyncClient, url: str) -> Optional[str]:
    """
    Fetch the article page at `url` and pull out the main body text
    (byline/nav/ads/comments stripped) via trafilatura.

    Returns None on any failure (network error, non-200, no extractable
    text) so callers can fall back to the RSS snippet.
    """
    try:
        response = await client.get(url, timeout=EXTRACT_TIMEOUT_SECONDS, follow_redirects=True)
        if response.status_code != 200 or not response.text:
            return None

        # trafilatura's extract() is a synchronous CPU-bound parse (lxml) —
        # run it off the event loop so a big/slow page can't stall polling.
        text = await asyncio.to_thread(
            trafilatura.extract,
            response.text,
            include_comments=False,
            include_tables=False,
            favor_recall=True,
        )
        if text:
            text = text.strip()
        return text or None
    except Exception as e:
        logger.debug(f"Full-content extraction failed for {url}: {e}")
        return None
