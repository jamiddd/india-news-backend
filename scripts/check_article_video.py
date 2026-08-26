"""
One-off diagnostic: looks up an article by a title substring and prints its
video_url/media_type, to check whether a specific story failed video
extraction or just hasn't been re-scraped since a fix.

Usage:
    python3 scripts/check_article_video.py "sugar ethanol"
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import text

from app.database import engine


async def main(title_substring: str):
    pattern = f"%{title_substring}%"
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                "SELECT id, source_id, title, url, image_url, video_url, media_type, published_at "
                "FROM articles WHERE title ILIKE :pattern ORDER BY published_at DESC LIMIT 10"
            ),
            {"pattern": pattern},
        )
        rows = result.fetchall()
        print(f"{len(rows)} article(s) matching {title_substring!r}:")
        for row in rows:
            print(row)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/check_article_video.py <title substring>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
