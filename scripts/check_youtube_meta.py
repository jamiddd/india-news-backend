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
    _fetch_youtube_video_meta,
)


async def main(video_ids: list[str]):
    async with CurlAsyncSession() as client:
        for video_id in video_ids:
            # The raw request first, so a bot/consent interstitial shows up as
            # itself rather than as a silent (None, None).
            try:
                # Unfollowed, mirroring the real lookup: the status and the
                # Location header are the whole Shorts signal.
                response = await client.get(
                    f"https://www.youtube.com/shorts/{video_id}",
                    timeout=15.0,
                    allow_redirects=False,
                    impersonate=IMPERSONATE,
                )
                body = response.text or ""
                length_match = _YOUTUBE_LENGTH_RE.search(body)
                location = response.headers.get("location") or ""
                print(f"{video_id}: HTTP {response.status_code}, {len(body)} bytes")
                print(f"  location:      {location or '(none)'}")
                print(f"  reads as:      {'Short' if response.status_code == 200 else ('regular' if f'/watch?v={video_id}' in location else 'UNKNOWN — bounced')}")
                print(f"  lengthSeconds: {length_match.group(1) if length_match else None}")
                if "consent." in location or "/sorry/" in location:
                    print("  !! redirected to a consent/captcha interstitial, not the video")
            except Exception as e:
                print(f"{video_id}: request failed: {e}")

            print(f"  _fetch_youtube_video_meta -> {await _fetch_youtube_video_meta(client, video_id)}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/check_youtube_meta.py <video_id> [<video_id> ...]")
        sys.exit(1)
    asyncio.run(main(sys.argv[1:]))
