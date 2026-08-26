"""
One-off cleanup for the false-positive JW Player detection bug: the initial
version of extractor.py's JW resolution matched a sitewide "recommended
video" widget div (e.g. The Hindu's article-end-video-container) on every
article regardless of topic, so many non-video articles got the same
video_url/media_type='video' as the widget's placeholder video (media id
hbfpegZO). extractor.py now only trusts a JSON-LD VideoObject block (see
_extract_jwplayer_media_id), which doesn't have this problem — this script
clears out the bad rows written before that fix landed.

Safe to run multiple times — a second run just finds 0 rows.

Usage:
    python3 scripts/clear_false_positive_videos.py
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import text

from app.database import engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# The exact false-positive media id observed in production.
BAD_MEDIA_ID = "hbfpegZO"


async def main():
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                "UPDATE articles SET video_url = NULL, media_type = CASE WHEN image_url IS NOT NULL THEN 'image' ELSE NULL END "
                "WHERE video_url LIKE :pattern"
            ),
            {"pattern": f"%{BAD_MEDIA_ID}%"},
        )
        logger.info(f"Cleared {result.rowcount} false-positive video row(s).")


if __name__ == "__main__":
    asyncio.run(main())
