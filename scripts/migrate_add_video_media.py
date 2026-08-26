"""
One-off migration for existing deployments, needed for video media scraping
support: adds articles.video_url and articles.media_type (see app/models.py's
Article and app/services/poller.py, which now extracts a video URL from RSS
media/enclosure tags or the article page's og:video meta tag alongside the
existing image extraction).

Safe to run multiple times — both column adds are IF NOT EXISTS.

Usage:
    python3 scripts/migrate_add_video_media.py
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


async def main():
    async with engine.begin() as conn:
        await conn.execute(text(
            "ALTER TABLE articles ADD COLUMN IF NOT EXISTS video_url TEXT"
        ))
        await conn.execute(text(
            "ALTER TABLE articles ADD COLUMN IF NOT EXISTS media_type VARCHAR(10)"
        ))
        logger.info("articles.video_url and articles.media_type are present.")


if __name__ == "__main__":
    asyncio.run(main())
