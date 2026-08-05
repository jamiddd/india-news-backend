import asyncio
import os
import sys

# Ensure root of repo/backend is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from sqlalchemy import desc

from app.database import AsyncSessionLocal
from app.models import StoryCluster, Article
from app.services.enrichment import enrich_cluster_with_ai

# Cap per run so a cron'd invocation can't burn an unbounded amount of API
# spend if a large backlog of unenriched clusters ever builds up.
DEFAULT_BATCH_LIMIT = 50

async def main(limit: int = DEFAULT_BATCH_LIMIT, force_all: bool = False):
    async with AsyncSessionLocal() as session:
        query = select(StoryCluster).options(
            selectinload(StoryCluster.articles).selectinload(Article.source)
        )
        if not force_all:
            # entities is only ever null on a cluster enrich_cluster_with_ai has
            # never touched (it always sets at least the rule-based baseline) —
            # use that as the "needs enrichment" signal so re-runs are cheap and
            # idempotent. Newest-first: freshly clustered stories matter more
            # than clearing out old backlog.
            query = query.where(StoryCluster.entities.is_(None))
        query = query.order_by(desc(StoryCluster.first_seen_at)).limit(limit)

        res = await session.execute(query)
        clusters = res.scalars().all()

        if not clusters:
            print("No unenriched clusters found — nothing to do.")
            return

        print(f"Enriching {len(clusters)} story cluster(s)...")
        for cluster in clusters:
            await enrich_cluster_with_ai(session, cluster)
        print(f"✅ Enriched {len(clusters)} story clusters with entity tags & framing angles!")

if __name__ == "__main__":
    # --all forces re-enrichment of every cluster regardless of current state
    # (e.g. after a prompt change) — normal cron runs should NOT pass this.
    force_all = "--all" in sys.argv
    asyncio.run(main(force_all=force_all))
