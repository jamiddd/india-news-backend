import asyncio
import os
import sys

# Ensure root of repo/backend is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from sqlalchemy import desc, or_, and_

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
            # entities.is_(None) alone isn't a safe "needs enrichment" signal:
            # enrich_cluster_with_ai() always sets entities/topics/framing
            # from the free rule-based baseline BEFORE it even attempts the
            # paid Anthropic call, so a cluster that hit that call failing
            # (e.g. the API key ran out of credit) still ends up with
            # non-null entities — permanently invisible to that check even
            # though it never actually got AI enrichment. ai_enriched is the
            # real signal (only set True on an actual successful API call —
            # see enrichment.py). Still also catch entities IS NULL for
            # clusters enrich_cluster_with_ai has never touched at all.
            #
            # Singleton clusters (article_count == 1) are excluded from the
            # ai_enriched retry side on purpose: enrich_cluster_with_ai's own
            # cost guardrail skips the Anthropic call entirely for them, so
            # they can never become ai_enriched — including them here would
            # just have this script re-select the same singletons forever,
            # crowding out real retries. They still get included the first
            # time via the entities IS NULL branch.
            query = query.where(
                or_(
                    StoryCluster.entities.is_(None),
                    and_(StoryCluster.ai_enriched.is_(False), StoryCluster.article_count >= 2),
                )
            )
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
