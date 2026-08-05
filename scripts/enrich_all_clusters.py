import asyncio
import os
import sys
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from app.database import AsyncSessionLocal
from app.models import StoryCluster, Article
from app.services.enrichment import enrich_cluster_with_ai

async def main():
    print("Running Entity Extraction & Framing Analysis enrichment across database clusters...")
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(StoryCluster).options(selectinload(StoryCluster.articles).selectinload(Article.source))
        )
        clusters = res.scalars().all()
        enriched_count = 0
        for cluster in clusters:
            await enrich_cluster_with_ai(session, cluster)
            enriched_count += 1
        print(f"✅ Enriched {enriched_count} story clusters with entity tags & framing angles!")

if __name__ == "__main__":
    asyncio.run(main())
