"""
One-off diagnostic: given an article id, prints its cluster_id/video_url,
then lists every article in that same cluster with their own video_url — to
check whether a video article's video_url is getting lost/hidden because
the app's "first article with media" pick landed on a different row in the
same cluster, or because the article was clustered separately from what the
app is showing.

Usage:
    python3 scripts/check_cluster_video.py <article_id>
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import text

from app.database import engine


async def main(article_id: int):
    async with engine.begin() as conn:
        result = await conn.execute(
            text("SELECT id, cluster_id, source_id, title, video_url, image_url FROM articles WHERE id = :id"),
            {"id": article_id},
        )
        article = result.fetchone()
        print(f"article {article_id}:", article)

        if not article or article.cluster_id is None:
            print("No cluster_id on this article — nothing more to check.")
            return

        result2 = await conn.execute(
            text(
                "SELECT id, source_id, title, video_url, image_url FROM articles "
                "WHERE cluster_id = :cluster_id ORDER BY published_at"
            ),
            {"cluster_id": article.cluster_id},
        )
        print(f"articles in cluster {article.cluster_id}:")
        for row in result2.fetchall():
            print(row)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/check_cluster_video.py <article_id>")
        sys.exit(1)
    asyncio.run(main(int(sys.argv[1])))
