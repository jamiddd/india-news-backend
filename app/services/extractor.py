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
# JW Player embeds (The Hindu and other publishers using it) never expose a
# direct playable file via og:video — og:video, when present at all, points
# at the *player page* (cdn.jwplayer.com/players/<mediaid>.html), which
# ExoPlayer can't play. The media id embedded in that URL (or in a
# `botr_<mediaid>_..._div` container div JW's embed script looks for) can be
# resolved through JW's public, unauthenticated delivery API into an actual
# HLS/mp4 source — see _resolve_jwplayer_video.
_JW_PLAYER_URL_RE = re.compile(r'cdn\.jwplayer\.com/players/([A-Za-z0-9]+)\.html', re.IGNORECASE)
_JW_BOTR_DIV_RE = re.compile(r'id=["\']botr_([A-Za-z0-9]+)_', re.IGNORECASE)


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


def _extract_jwplayer_media_id(html: str) -> Optional[str]:
    match = _JW_PLAYER_URL_RE.search(html) or _JW_BOTR_DIV_RE.search(html)
    return match.group(1) if match else None


async def _resolve_jwplayer_video(client: AsyncSession, media_id: str) -> Optional[str]:
    """
    Hits JW Player's public, unauthenticated delivery API for a given media
    id and returns a directly-playable source URL — the HLS manifest
    (.m3u8) if present, since ExoPlayer handles adaptive bitrate switching
    natively, otherwise the first progressive mp4 fallback.
    """
    try:
        response = await client.get(
            f"https://cdn.jwplayer.com/v2/media/{media_id}", timeout=EXTRACT_TIMEOUT_SECONDS
        )
        if response.status_code != 200:
            return None
        data = response.json()
        sources = (data.get("playlist") or [{}])[0].get("sources") or []
        mp4_fallback = None
        for source in sources:
            file_url = source.get("file")
            if not file_url:
                continue
            if source.get("type") == "application/vnd.apple.mpegurl":
                return file_url
            if mp4_fallback is None and source.get("type") == "video/mp4":
                mp4_fallback = file_url
        return mp4_fallback
    except Exception as e:
        logger.debug(f"JW Player resolution failed for media id {media_id}: {e}")
        return None


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
        if not og_video_url:
            jw_media_id = _extract_jwplayer_media_id(response.text)
            if jw_media_id:
                og_video_url = await _resolve_jwplayer_video(client, jw_media_id)
        return ExtractedArticle(content, og_image_url, og_video_url)
    except Exception as e:
        logger.debug(f"Full-content extraction failed for {url}: {e}")
        return ExtractedArticle(None, None)
