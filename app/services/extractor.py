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

# Brightcove embeds (Al Jazeera and other publishers using it) show up as a
# players.brightcove.net URL carrying the account id, player id, and a
# videoId query param. Unlike JW's sitewide widget, this id genuinely
# differs per page — but that turned out to be because Al Jazeera injects a
# rotating "featured video" widget into EVERY page, video or not, so a
# per-article id alone doesn't prove the page's own content is that video.
# The actual signal: a real video page's ld+json has a VideoObject entry and
# NO NewsArticle entry, while a text article that merely carries the
# featured-video widget has both. So, same restriction shape as JW: only
# trust an embedUrl found inside a VideoObject block, and only when the
# page isn't also typed as a NewsArticle.
_BRIGHTCOVE_EMBED_RE = re.compile(
    r'players\.brightcove\.net/(\d+)/([A-Za-z0-9_-]+)/index(?:\.min)?\.(?:html|js)\?videoId=(\d+)',
    re.IGNORECASE,
)
_ARTICLE_LD_TYPES = {"NewsArticle", "Article", "ReportageNewsArticle", "BlogPosting"}

# YouTube embeds (Hindustan Times Videos, Livemint Videos) show up the same
# structural way as Brightcove: a VideoObject ld+json block whose
# contentUrl/embedUrl points at youtube.com/youtu.be. There's no playable
# stream to resolve here — YouTube doesn't expose one, and scraping one out
# via yt-dlp-style signature reverse-engineering would be fragile and
# against YouTube's terms — so this just normalizes the id into a stable
# embed URL for the app to load in an actual YouTube (IFrame) player.
# Verified HT/Livemint's regular text articles carry no VideoObject at all
# (unlike Al Jazeera's sitewide Brightcove widget), but the NewsArticle
# exclusion is kept anyway as the same cheap safety net.
_YOUTUBE_CONTENT_URL_RE = re.compile(
    r'(?:youtube\.com/embed/|youtube\.com/watch\?v=|youtube\.com/shorts/|youtu\.be/)([A-Za-z0-9_-]{11})',
    re.IGNORECASE,
)
# Set on any video_url this module hands back for a YouTube video, so callers
# (poller.py) can tell "a video we can play ourselves" from "a video only
# YouTube can play" without re-parsing the URL.
_YOUTUBE_EMBED_PREFIX = "https://www.youtube.com/embed/"
# Reading the Shorts marker and the real duration off YouTube's own page,
# rather than trusting the publisher's ld+json: verified that Free Press
# Journal's VideoObject claims duration PT5M02S for a video YouTube reports
# as 23 seconds, so the publisher figure is not usable for the app's badge.
_YOUTUBE_LENGTH_RE = re.compile(r'"lengthSeconds":"(\d+)"')
_youtube_meta_cache: dict[str, tuple[Optional[bool], Optional[int]]] = {}
# The player's policy key (needed to call Brightcove's Playback API) isn't on
# the article page — it's baked into that player's own bundle at
# players.brightcove.net/<account>/<player>_default/index.min.js. It's fixed
# per (account, player) pair, not per video, so cache it in-process instead
# of re-fetching the player bundle for every single article.
_BRIGHTCOVE_POLICY_KEY_RE = re.compile(r'policyKey:"([^"]+)"')
_brightcove_policy_key_cache: dict[tuple[str, str], Optional[str]] = {}


@dataclass
class ExtractedArticle:
    content: Optional[str]
    og_image_url: Optional[str]
    og_video_url: Optional[str] = None
    # Both None unless og_video_url is a YouTube video (see
    # _fetch_youtube_video_meta). is_short drives the app's fullscreen
    # orientation; duration_seconds drives the feed card's badge.
    video_is_short: Optional[bool] = None
    video_duration_seconds: Optional[int] = None


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


def _ld_json_entries(html: str) -> list[dict]:
    entries = []
    for block in _LD_JSON_RE.findall(html):
        try:
            data = json.loads(block)
        except (json.JSONDecodeError, ValueError):
            continue
        candidates = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        entries.extend(c for c in candidates if isinstance(c, dict))
    return entries


def _ld_json_types(entries: list[dict]) -> set[str]:
    types = set()
    for entry in entries:
        entry_type = entry.get("@type")
        if isinstance(entry_type, list):
            types.update(entry_type)
        elif entry_type:
            types.add(entry_type)
    return types


def _ld_json_video_object_urls(entries: list[dict]) -> list[str]:
    return [
        entry.get("embedUrl") or entry.get("contentUrl") or ""
        for entry in entries
        if entry.get("@type") == "VideoObject"
    ]


def _extract_brightcove_embed(html: str) -> Optional[tuple[str, str, str]]:
    """
    Only trusts a VideoObject found on a page that ISN'T also typed as a
    text-article schema (NewsArticle/Article/etc) — Al Jazeera injects the
    same rotating "featured video" Brightcove widget into every page, video
    or not, so a NewsArticle co-occurring with VideoObject means the
    VideoObject is that sitewide widget, not this page's own content.
    """
    entries = _ld_json_entries(html)
    if _ld_json_types(entries) & _ARTICLE_LD_TYPES:
        return None
    for url in _ld_json_video_object_urls(entries):
        match = _BRIGHTCOVE_EMBED_RE.search(url)
        if match:
            return match.group(1), match.group(2), match.group(3)
    return None


def _extract_youtube_video_id(html: str) -> Optional[str]:
    """
    Unlike Brightcove/Al Jazeera, HT/Livemint's own video pages carry
    NewsArticle *and* VideoObject together, and — verified against their
    regular text articles — never emit a VideoObject at all outside a real
    video page. So no NewsArticle exclusion here; a VideoObject's presence
    at all is already the correct signal for these publishers.
    """
    entries = _ld_json_entries(html)
    for url in _ld_json_video_object_urls(entries):
        match = _YOUTUBE_CONTENT_URL_RE.search(url)
        if match:
            return match.group(1)
    return None


def is_youtube_video_url(url: Optional[str]) -> bool:
    """
    True for a video only YouTube can play. poller.py uses this to keep such
    a video out of media_type="video": the app never plays a YouTube video
    inline (it shows the article image with a duration badge that opens a
    dedicated fullscreen screen), so ranking it as a video story would
    over-promote a card that renders as an ordinary image card.
    """
    return bool(url) and bool(_YOUTUBE_CONTENT_URL_RE.search(url))


async def _fetch_youtube_video_meta(
    client: AsyncSession, video_id: str
) -> tuple[Optional[bool], Optional[int]]:
    """
    Returns (is_short, duration_seconds) for a YouTube video. Either element
    may be None for "unknown", and callers must treat it as such rather than
    as a negative — a Short shown letterboxed in a landscape player looks
    broken, and a badge must say "Watch" rather than invent a runtime.

    Shorts-ness is decided by the redirect status alone, before any body
    parsing: /shorts/<id> stays 200 for a real Short and 303s to /watch for
    anything else. That distinction is made by YouTube's router and survives
    the bot/consent interstitials a datacenter IP draws, which body parsing
    does not — in production the followed /watch page comes back without the
    "lengthSeconds" the same request yields from a laptop. An earlier version
    read Shorts-ness out of the body instead, which reported a confident
    is_short=False whenever the real page hadn't been served at all.

    A redirect anywhere other than /watch?v=<id> (a consent screen, /sorry/)
    means we were bounced, not answered, so it yields unknown.

    The desktop TLS/UA identity from IMPERSONATE is load-bearing, not
    incidental: with a *mobile* user agent YouTube 302s both cases to the
    same place and the distinction disappears entirely.

    Duration is best-effort on top of that. It's read from whichever page we
    end up with, and stays None when that page is gated.
    """
    if video_id in _youtube_meta_cache:
        return _youtube_meta_cache[video_id]

    result: tuple[Optional[bool], Optional[int]] = (None, None)
    try:
        response = await client.get(
            f"https://www.youtube.com/shorts/{video_id}",
            timeout=EXTRACT_TIMEOUT_SECONDS,
            allow_redirects=False,
            impersonate=IMPERSONATE,
        )
        is_short: Optional[bool] = None
        body = ""
        if response.status_code == 200:
            is_short = True
            body = response.text or ""
        elif response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("location") or ""
            if f"/watch?v={video_id}" in location:
                is_short = False
                # Best-effort duration only; this is the request that comes
                # back gated from a datacenter IP.
                try:
                    followed = await client.get(
                        location,
                        timeout=EXTRACT_TIMEOUT_SECONDS,
                        allow_redirects=True,
                        impersonate=IMPERSONATE,
                    )
                    body = followed.text or ""
                except Exception as e:
                    logger.debug(f"YouTube watch-page lookup failed for {video_id}: {e}")

        if is_short is not None:
            match = _YOUTUBE_LENGTH_RE.search(body)
            result = (is_short, int(match.group(1)) if match else None)
    except Exception as e:
        logger.debug(f"YouTube metadata lookup failed for {video_id}: {e}")

    _youtube_meta_cache[video_id] = result
    return result


async def _resolve_brightcove_policy_key(client: AsyncSession, account_id: str, player_id: str) -> Optional[str]:
    cache_key = (account_id, player_id)
    if cache_key in _brightcove_policy_key_cache:
        return _brightcove_policy_key_cache[cache_key]
    policy_key = None
    try:
        response = await client.get(
            f"https://players.brightcove.net/{account_id}/{player_id}/index.min.js",
            timeout=EXTRACT_TIMEOUT_SECONDS,
        )
        if response.status_code == 200:
            match = _BRIGHTCOVE_POLICY_KEY_RE.search(response.text)
            if match:
                policy_key = match.group(1)
    except Exception as e:
        logger.debug(f"Brightcove policy key lookup failed for {account_id}/{player_id}: {e}")
    _brightcove_policy_key_cache[cache_key] = policy_key
    return policy_key


async def _resolve_brightcove_video(
    client: AsyncSession, account_id: str, video_id: str, policy_key: str
) -> Optional[str]:
    """
    Hits Brightcove's Playback API for a given account/video id and returns a
    directly-playable source URL — the HLS manifest (.m3u8) if present,
    since ExoPlayer handles adaptive bitrate switching natively, otherwise
    the first progressive mp4 fallback.

    Brightcove lists the same manifest/file twice, once as http:// and once
    as https://, back to back — picking the first match of a given type
    would silently prefer http:// (it happens to come first), which the app
    can't play at all (usesCleartextTraffic is false in the manifest). So
    this only ever returns an https:// src, upgrading a bare http:// one if
    that's the only variant a given source happens to offer.
    """
    try:
        response = await client.get(
            f"https://edge.api.brightcove.com/playback/v1/accounts/{account_id}/videos/{video_id}",
            headers={"Accept": f"application/json;pk={policy_key}"},
            timeout=EXTRACT_TIMEOUT_SECONDS,
        )
        if response.status_code != 200:
            return None
        data = response.json()
        sources = data.get("sources") or []
        m3u8_url = None
        mp4_fallback = None
        for source in sources:
            src = source.get("src")
            if not src:
                continue
            if m3u8_url is None and source.get("type") == "application/x-mpegURL":
                m3u8_url = src
            elif mp4_fallback is None and source.get("container") == "MP4":
                mp4_fallback = src
        chosen = m3u8_url or mp4_fallback
        if chosen and chosen.startswith("http://"):
            chosen = "https://" + chosen[len("http://"):]
        return chosen
    except Exception as e:
        logger.debug(f"Brightcove resolution failed for account {account_id} video {video_id}: {e}")
        return None


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
      widget (resolved via JW's delivery API), Brightcove embed (resolved
      via Brightcove's Playback API), a YouTube embed (normalized to a
      stable youtube.com/embed/<id> URL, and annotated with its Shorts flag
      and real duration — there's no raw stream to resolve, the app plays
      this only in its dedicated fullscreen YouTube screen), then a
      native <video>/<source> tag's direct .mp4/.m3u8/.webm src as a last
      resort

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
            brightcove_embed = _extract_brightcove_embed(response.text)
            if brightcove_embed:
                account_id, player_id, video_id = brightcove_embed
                policy_key = await _resolve_brightcove_policy_key(client, account_id, player_id)
                if policy_key:
                    og_video_url = await _resolve_brightcove_video(client, account_id, video_id, policy_key)
        if not og_video_url:
            youtube_video_id = _extract_youtube_video_id(response.text)
            if youtube_video_id:
                og_video_url = f"{_YOUTUBE_EMBED_PREFIX}{youtube_video_id}"
        if not og_video_url:
            og_video_url = _extract_video_tag(response.text, url)

        # Annotated here rather than inside the YouTube branch above, because
        # that isn't the only step that can hand back a YouTube URL — an
        # og:video meta tag pointing at youtube.com is resolved earlier and
        # would otherwise skip the lookup, leaving the app with a YouTube
        # video it can't tell the shape or length of.
        video_is_short: Optional[bool] = None
        video_duration_seconds: Optional[int] = None
        youtube_match = _YOUTUBE_CONTENT_URL_RE.search(og_video_url) if og_video_url else None
        if youtube_match:
            video_is_short, video_duration_seconds = await _fetch_youtube_video_meta(
                client, youtube_match.group(1)
            )
        return ExtractedArticle(
            content, og_image_url, og_video_url, video_is_short, video_duration_seconds
        )
    except Exception as e:
        logger.debug(f"Full-content extraction failed for {url}: {e}")
        return ExtractedArticle(None, None)
