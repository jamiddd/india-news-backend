"""Re-enrich clusters whose stored summary is malformed: more than
MAX_SUMMARY_BULLETS bullets, a bullet over MAX_BULLET_CHARS, or a
missing-space-after-period run-on (e.g. "spreads.Benchmark"). These are
clusters that were already ai_enriched *before* enrichment.py's
_clamp_bullets() guardrail existed, so re-running enrichment now produces a
clamped, well-formed summary from the same source articles.

Unlike scripts/enrich_all_clusters.py (which targets clusters that have
never been enriched), this targets clusters that HAVE ai_enriched=True but
whose summary shape indicates it predates the guardrail — so it force-skips
the "already enriched, leave it alone" check that script relies on.
"""
import asyncio
import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import AsyncSessionLocal
from app.models import StoryCluster, Article
from app.services.enrichment import (
    enrich_cluster_with_ai,
    MAX_SUMMARY_BULLETS,
    MAX_BULLET_CHARS,
)

# Cap per run for the same reason enrich_all_clusters.py caps itself: each
# re-enrichment is a paid Anthropic call, and a cron'd/unattended invocation
# shouldn't be able to burn unbounded spend against however large the
# malformed-summary backlog turns out to be.
DEFAULT_BATCH_LIMIT = 50

RUN_ON_PERIOD = re.compile(r"\.[A-Z]")


def summary_is_malformed(summary: str | None) -> bool:
    if not summary:
        return False
    bullets = [b.strip() for b in summary.split("\n") if b.strip()]
    # Bullets are stored as "• text" (see enrichment.py's
    # "\n• ".join(...)) — strip the marker before measuring length.
    bullets = [b.lstrip("•").strip() for b in bullets]
    if len(bullets) > MAX_SUMMARY_BULLETS:
        return True
    for bullet in bullets:
        if len(bullet) > MAX_BULLET_CHARS:
            return True
        if RUN_ON_PERIOD.search(bullet):
            return True
    return False


async def main(limit: int = DEFAULT_BATCH_LIMIT, dry_run: bool = False):
    async with AsyncSessionLocal() as session:
        # ai_enriched=True + article_count >= 2 narrows to clusters that
        # went through the Anthropic path at all (enrich_cluster_with_ai's
        # cost guardrail skips singletons entirely — see enrichment.py —
        # so they can never be ai_enriched and re-running them would just
        # spend nothing and change nothing).
        query = select(StoryCluster).options(
            selectinload(StoryCluster.articles).selectinload(Article.source)
        ).where(
            StoryCluster.ai_enriched.is_(True),
            StoryCluster.article_count >= 2,
        )
        res = await session.execute(query)
        clusters = res.scalars().all()

        malformed = [c for c in clusters if summary_is_malformed(c.summary)]

        if not malformed:
            print("No malformed summaries found — nothing to do.")
            return

        batch = malformed[:limit]
        print(f"Found {len(malformed)} cluster(s) with malformed summaries; "
              f"re-enriching {len(batch)} (limit={limit}).")

        if dry_run:
            for c in batch:
                print(f"  would re-enrich cluster #{c.id}: {c.headline!r}")
            print("Dry run — no API calls made, nothing written.")
            return

        enriched = 0
        for cluster in batch:
            try:
                await enrich_cluster_with_ai(session, cluster)
                enriched += 1
                print(f"  ✅ re-enriched cluster #{cluster.id}")
            except Exception as e:
                # enrich_cluster_with_ai already swallows its own Anthropic
                # errors internally (falls back to rule-based baseline), so
                # anything raised past it is unexpected — log and move on
                # rather than letting one bad cluster kill the whole batch.
                print(f"  ⚠️  cluster #{cluster.id} failed: {e}")

        print(f"Done — re-enriched {enriched}/{len(batch)} cluster(s). "
              f"{len(malformed) - len(batch)} still remaining beyond this run's limit.")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    limit = DEFAULT_BATCH_LIMIT
    for arg in sys.argv[1:]:
        if arg.startswith("--limit="):
            limit = int(arg.split("=", 1)[1])
    asyncio.run(main(limit=limit, dry_run=dry_run))
