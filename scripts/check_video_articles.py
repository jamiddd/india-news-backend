"""
One-off diagnostic: lists the most recent articles that have a video_url set,
to confirm the video-scraping addition (see app/services/poller.py,
image_extractor.py, extractor.py, and migrate_add_video_media.py) is
actually picking anything up in production.

Usage:
    python3 scripts/check_video_articles.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import text

from app.database import engine


async def main():
    async with engine.begin() as conn:
        result = await conn.execute(text(
            "SELECT id, source_id, video_url, media_type, published_at "
            "FROM articles WHERE video_url IS NOT NULL "
            "ORDER BY published_at DESC LIMIT 20"
        ))
        rows = result.fetchall()
        print(f"{len(rows)} article(s) with video_url:")
        for row in rows:
            print(row)


if __name__ == "__main__":
    asyncio.run(main())
