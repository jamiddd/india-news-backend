"""
One-off diagnostic: looks up articles by a title substring and prints their
video columns straight from the database, to check whether a specific story
failed video extraction or just hasn't been re-scraped since a fix.

Reads the DB directly, so it also settles questions the API can't: the
/clusters list endpoint is cached for 30s, and behind a load balancer it
isn't obvious which host answered.

With --extract it also re-runs extract_full_content() against each article's
live URL and prints what comes back, which separates "the row was never
updated" from "the page no longer yields a video to this machine". Nothing
is written either way.

Usage:
    python3 scripts/check_article_video.py "samay raina"
    python3 scripts/check_article_video.py "samay raina" --extract
"""
import argparse
import asyncio
import os
import sys

from curl_cffi.requests import AsyncSession as CurlAsyncSession

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import text

from app.database import engine
from app.services.extractor import EXTRACT_TIMEOUT_SECONDS, IMPERSONATE, extract_full_content


async def main(title_substring: str, extract: bool):
    pattern = f"%{title_substring}%"
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                "SELECT id, source_id, title, url, image_url, video_url, media_type, "
                "video_is_short, video_duration_seconds, published_at "
                "FROM articles WHERE title ILIKE :pattern ORDER BY published_at DESC LIMIT 10"
            ),
            {"pattern": pattern},
        )
        rows = result.fetchall()

    print(f"{len(rows)} article(s) matching {title_substring!r}:\n")
    for row in rows:
        print(f"  id={row.id} source={row.source_id} published={row.published_at}")
        print(f"    title:      {row.title[:90]}")
        print(f"    url:        {row.url}")
        print(f"    media_type: {row.media_type!r}   image: {'yes' if row.image_url else 'no'}")
        print(f"    video_url:  {row.video_url}")
        print(f"    is_short={row.video_is_short}  duration={row.video_duration_seconds}")
        print()

    if not extract:
        return

    print("Re-running extraction (no writes):\n")
    async with CurlAsyncSession() as client:
        for row in rows:
            extraction = await extract_full_content(client, row.url, row.title)
            print(f"  id={row.id}")
            print(f"    content:  {'yes' if extraction.content else 'NONE'}")
            print(f"    image:    {extraction.og_image_url}")
            print(f"    video:    {extraction.og_video_url}")
            print(f"    is_short={extraction.video_is_short}  duration={extraction.video_duration_seconds}")
            if extraction.content is None and extraction.og_image_url is None:
                # All three fields null is extract_full_content's early-return
                # path: the fetch itself failed (non-200, empty body, or an
                # exception), so nothing was parsed. That is a different
                # problem from a page that parsed but carried no video, and
                # only the raw request can tell them apart.
                await _report_raw_fetch(client, row.url)
            elif not extraction.og_video_url:
                print("    !! parsed fine, but this page carries no video")
            print()


async def _report_raw_fetch(client, url: str):
    """Repeats the fetch extract_full_content makes, reporting how it failed."""
    try:
        response = await client.get(
            url,
            timeout=EXTRACT_TIMEOUT_SECONDS,
            allow_redirects=True,
            impersonate=IMPERSONATE,
        )
        body = response.text or ""
        print(f"    !! fetch returned HTTP {response.status_code}, {len(body)} bytes")
        print(f"       landed on: {response.url}")
        if response.status_code != 200:
            print(f"       body head: {body[:200]!r}")
    except Exception as e:
        print(f"    !! fetch raised {type(e).__name__}: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("title_substring")
    parser.add_argument("--extract", action="store_true", help="Re-run extraction against the live URL.")
    args = parser.parse_args()
    asyncio.run(main(args.title_substring, args.extract))
