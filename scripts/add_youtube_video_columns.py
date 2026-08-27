"""
One-off migration: adds `video_is_short` and `video_duration_seconds` to
`articles`, and demotes existing YouTube-only rows out of media_type="video".

Both columns describe a YouTube video and stay NULL for a direct-stream one
— see app/models.py's Article and extractor._fetch_youtube_video_meta.

The demotion is the ranking half of the same decision: the app never plays
a YouTube video inline, it renders the article image with a duration badge
that opens a dedicated fullscreen screen, so such a story must not rank as
a video story. video_url is left in place — it's what that screen plays.
Rows are demoted to "image" when they have an image_url and NULL otherwise,
matching what poller.py now writes at ingest.

The new columns are left NULL by this script; scripts/rescrape_video_urls.py
populates them by re-running extraction.

Safe to run multiple times (IF NOT EXISTS, and the demotion is idempotent).

Usage:
    python3 scripts/add_youtube_video_columns.py
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

# Mirrors extractor._YOUTUBE_CONTENT_URL_RE's accepted forms. SQL LIKE has no
# alternation, so each is matched separately.
YOUTUBE_URL_PATTERNS = [
    "%youtube.com/embed/%",
    "%youtube.com/watch?v=%",
    "%youtube.com/shorts/%",
    "%youtu.be/%",
]


async def main():
    async with engine.begin() as conn:
        await conn.execute(text(
            "ALTER TABLE articles ADD COLUMN IF NOT EXISTS video_is_short BOOLEAN"
        ))
        await conn.execute(text(
            "ALTER TABLE articles ADD COLUMN IF NOT EXISTS video_duration_seconds INTEGER"
        ))
        logger.info("articles.video_is_short / video_duration_seconds columns are present.")

        clause = " OR ".join(f"video_url LIKE :p{i}" for i in range(len(YOUTUBE_URL_PATTERNS)))
        params = {f"p{i}": p for i, p in enumerate(YOUTUBE_URL_PATTERNS)}
        result = await conn.execute(
            text(
                "UPDATE articles SET media_type = CASE WHEN image_url IS NOT NULL THEN 'image' ELSE NULL END "
                f"WHERE media_type = 'video' AND ({clause})"
            ),
            params,
        )
        logger.info(f"Demoted {result.rowcount} YouTube-only article(s) out of media_type='video'.")


if __name__ == "__main__":
    asyncio.run(main())
