"""Best-effort check that a direct-stream video is long enough to be worth showing.

Some publishers attach a few-second loop or teaser as a story's video. The Watch tab
is for real clips, so those are dropped at ingest. Two signals, in order:

  * duration — read from an HLS manifest's segment lengths (the only direct-stream
    format that states it without downloading media), and
  * size — the Content-Length of a plain file, when no duration is known. A file
    under MIN_VIDEO_BYTES is a few seconds at any watchable bitrate.

Everything fails open: a timeout, an error, a live manifest or a missing header means
"unknown", and unknown is kept — dropping a real video over a flaky HEAD is worse
than showing a short one, which the app's own duration check also catches.
"""
import logging
import re
from typing import Optional, TYPE_CHECKING
from urllib.parse import urljoin, urlparse

from app.services.extractor import IMPERSONATE

if TYPE_CHECKING:
    from curl_cffi.requests import AsyncSession as CurlAsyncSession

logger = logging.getLogger(__name__)

MIN_VIDEO_SECONDS = 10
MIN_VIDEO_BYTES = 1_000_000
PROBE_TIMEOUT_SECONDS = 5

_EXTINF_RE = re.compile(r"#EXTINF:([0-9.]+)")


def _is_hls(url: str) -> bool:
    return urlparse(url).path.lower().endswith(".m3u8")


def hls_duration_seconds(manifest: str) -> Optional[float]:
    """Total length of a finished (VOD) media playlist; None for a live one or a
    master playlist, which carry no total."""
    if "#EXT-X-ENDLIST" not in manifest:
        return None
    total = sum(float(m) for m in _EXTINF_RE.findall(manifest))
    return total if total > 0 else None


def first_variant_uri(master: str) -> Optional[str]:
    """The first rendition a master playlist points at (any one has the full length)."""
    lines = master.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("#EXT-X-STREAM-INF"):
            for follow in lines[i + 1:]:
                follow = follow.strip()
                if follow and not follow.startswith("#"):
                    return follow
    return None


async def _get_text(client: "CurlAsyncSession", url: str) -> Optional[str]:
    response = await client.get(url, timeout=PROBE_TIMEOUT_SECONDS, impersonate=IMPERSONATE)
    return response.text if response.status_code < 400 else None


async def probe_direct_video(
    client: "CurlAsyncSession", url: str
) -> tuple[Optional[int], Optional[int]]:
    """(duration_seconds, size_bytes) — each None when it couldn't be determined."""
    try:
        if _is_hls(url):
            text = await _get_text(client, url)
            if text and "#EXT-X-STREAM-INF" in text:
                variant = first_variant_uri(text)
                text = await _get_text(client, urljoin(url, variant)) if variant else None
            seconds = hls_duration_seconds(text) if text else None
            return (round(seconds) if seconds is not None else None), None
        response = await client.head(
            url, timeout=PROBE_TIMEOUT_SECONDS, impersonate=IMPERSONATE, allow_redirects=True
        )
        if response.status_code >= 400:
            return None, None
        length = response.headers.get("content-length")
        return None, int(length) if length and length.isdigit() else None
    except Exception:
        logger.debug("video probe failed for %s", url, exc_info=True)
        return None, None


def is_too_short(duration_seconds: Optional[int], size_bytes: Optional[int]) -> bool:
    if duration_seconds is not None:
        return duration_seconds < MIN_VIDEO_SECONDS
    return size_bytes is not None and size_bytes < MIN_VIDEO_BYTES
