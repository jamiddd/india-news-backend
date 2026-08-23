"""
Read-only inspection tool for the feed ranking redesign's piece 1 (global
importance): prints the top entity_stats rows by reactivation ratio
(mention_count_decayed / baseline_rate) and a sample of story_clusters with
non-zero entity_boost. Use this to sanity-check that the signal actually
promotes entities/stories that had a real recent spike, before deciding
whether/how to blend entity_boost into live /clusters ranking.

Usage (inside the app container, so DATABASE_URL is set):
    python3 scripts/check_entity_stats.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import text

from app.database import engine


async def main():
    async with engine.begin() as conn:
        result = await conn.execute(text("SELECT COUNT(*) FROM entity_stats"))
        print(f"entity_stats rows: {result.scalar()}\n")

        print("Top 15 by reactivation ratio (mention_count_decayed / baseline_rate):")
        result = await conn.execute(text("""
            SELECT entity_key, display_name, mention_count_decayed, baseline_rate,
                   mention_count_decayed / GREATEST(baseline_rate, 0.05) AS ratio,
                   last_mentioned_at
            FROM entity_stats
            ORDER BY ratio DESC
            LIMIT 15
        """))
        for row in result:
            print(f"  {row.entity_key:45s} ratio={row.ratio:6.2f}  mentions={row.mention_count_decayed:.3f}  "
                  f"baseline={row.baseline_rate:.3f}  last_seen={row.last_mentioned_at}")

        print("\nTop 15 clusters by entity_boost:")
        result = await conn.execute(text("""
            SELECT id, headline, distinct_source_count, headline_score, entity_boost
            FROM story_clusters
            WHERE entity_boost > 0
            ORDER BY entity_boost DESC
            LIMIT 15
        """))
        for row in result:
            print(f"  #{row.id:6d} boost={row.entity_boost:6.2f}  sources={row.distinct_source_count}  "
                  f"headline_score={row.headline_score:.3f}  {row.headline[:80]}")


if __name__ == "__main__":
    asyncio.run(main())
