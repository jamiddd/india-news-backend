"""
Diagnostic: run extractor._fetch_youtube_video_meta from *this* machine for
one or more YouTube video ids and print what comes back.

Written because the Shorts flag and duration came back NULL after a rescrape,
and the lookup's two plausible failure modes are indistinguishable from the
database: either the rescrape never selected the row, or YouTube answered the
server differently than it answers a developer laptop (datacenter IPs draw
bot/consent interstitials that a residential one doesn't). Running this on the
droplet separates them — real output means the lookup works and the rescrape
scope was wrong; (None, None) means YouTube treats the server differently.

Usage:
    python3 scripts/check_youtube_meta.py 3zgjo_HbL_o
"""
import asyncio
import os
import sys

from curl_cffi.requests import AsyncSession as CurlAsyncSession

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.extractor import (
    IMPERSONATE,
    _YOUTUBE_LENGTH_RE,
    _YOUTUBE_SHORTS_MARKER,
    _fetch_youtube_video_meta,
)


async def main(video_ids: list[str]):
    async with CurlAsyncSession() as client:
        for video_id in video_ids:
            # The raw request first, so a bot/consent interstitial shows up as
            # itself rather than as a silent (None, None).
            try:
                response = await client.get(
                    f"https://www.youtube.com/shorts/{video_id}",
                    timeout=15.0,
                    allow_redirects=True,
                    impersonate=IMPERSONATE,
                )
                body = response.text or ""
                length_match = _YOUTUBE_LENGTH_RE.search(body)
                print(f"{video_id}: HTTP {response.status_code}, {len(body)} bytes")
                print(f"  final url:     {response.url}")
                print(f"  shorts marker: {_YOUTUBE_SHORTS_MARKER in body}")
                print(f"  lengthSeconds: {length_match.group(1) if length_match else None}")
                if "consent" in str(response.url).lower() or "captcha" in body.lower():
                    print("  !! looks like a consent/captcha interstitial, not the video page")
            except Exception as e:
                print(f"{video_id}: request failed: {e}")

            print(f"  _fetch_youtube_video_meta -> {await _fetch_youtube_video_meta(client, video_id)}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/check_youtube_meta.py <video_id> [<video_id> ...]")
        sys.exit(1)
    asyncio.run(main(sys.argv[1:]))
