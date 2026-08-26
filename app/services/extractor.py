import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urljoin

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
# ExoPlayer can't play. The media id embedded in that URL can be resolved
# through JW's public, unauthenticated delivery API into an actual HLS/mp4
# source — see _resolve_jwplayer_video.
#
# The media id must come from a <script type="application/ld+json"> block
# whose @type is VideoObject — NOT a bare page-wide text search for a
# `cdn.jwplayer.com/players/<id>.html` or `botr_<id>_` div. Publishers embed
# a generic "recommended video" JW widget (e.g. The Hindu's
# `article-end-video-container`) identically on every article regardless of
# topic; a text search matches that widget's id on any page and produces a
# video_url for articles that have nothing to do with that video. The
# structured VideoObject block only exists on the article that IS that
# video, so restricting to it is what actually distinguishes real content
# from the sitewide widget.
_LD_JSON_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.IGNORECASE | re.DOTALL
)
_JW_PLAYER_URL_RE = re.compile(r'cdn\.jwplayer\.com/players/([A-Za-z0-9]+)\.html', re.IGNORECASE)

# Last-resort fallback for publishers that embed a native HTML5 <video> tag
# (a direct .mp4/.m3u8 src or one/more <source> children) instead of an
# og:video meta tag or a JW Player widget — e.g. Sky Sports. Unlike the JW
# path there's no structured VideoObject to confirm the video actually
# belongs to this article, so this only fires when og:video and JW both
# come up empty, and only for src values that look like a real media file
# (mp4/m3u8/webm) rather than a poster image or empty placeholder.
_VIDEO_TAG_RE = re.compile(r'<video\b[^>]*>.*?</video\s*>', re.IGNORECASE | re.DOTALL)
_VIDEO_TAG_SELF_CLOSING_RE = re.compile(r'<video\b[^>]*/?>', re.IGNORECASE)
_SRC_ATTR_RE = re.compile(r'\bsrc=["\']([^"\']+)["\']', re.IGNORECASE)
_MEDIA_FILE_RE = re.compile(r'\.(m3u8|mp4|webm)(\?|$)', re.IGNORECASE)


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
    for block in _LD_JSON_RE.findall(html):
        try:
            data = json.loads(block)
        except (json.JSONDecodeError, ValueError):
            continue
        # A page can carry several ld+json blocks (Organization, BreadcrumbList,
        # etc.) or one block with an @graph list of several typed entries —
        # check both shapes for the one that's actually a VideoObject.
        candidates = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        for entry in candidates:
            if not isinstance(entry, dict) or entry.get("@type") != "VideoObject":
                continue
            content_url = entry.get("contentUrl") or ""
            match = _JW_PLAYER_URL_RE.search(content_url)
            if match:
                return match.group(1)
    return None


def _extract_video_tag(html: str, base_url: str) -> Optional[str]:
    webm_fallback = None
    mp4_fallback = None
    for tag_re in (_VIDEO_TAG_RE, _VIDEO_TAG_SELF_CLOSING_RE):
        for block in tag_re.findall(html):
            for src in _SRC_ATTR_RE.findall(block):
                if not _MEDIA_FILE_RE.search(src):
                    continue
                resolved = urljoin(base_url, src)
                lowered = resolved.lower()
                if ".m3u8" in lowered:
                    return resolved
                if mp4_fallback is None and ".mp4" in lowered:
                    mp4_fallback = resolved
                elif webm_fallback is None and ".webm" in lowered:
                    webm_fallback = resolved
    return mp4_fallback or webm_fallback


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
    - a playable video URL, tried in order: og:video meta tag, JW Player
      widget (resolved via JW's delivery API), then a native <video>/<source>
      tag's direct .mp4/.m3u8/.webm src as a last resort

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
        if not og_video_url:
            og_video_url = _extract_video_tag(response.text, url)
        return ExtractedArticle(content, og_image_url, og_video_url)
    except Exception as e:
        logger.debug(f"Full-content extraction failed for {url}: {e}")
        return ExtractedArticle(None, None)
