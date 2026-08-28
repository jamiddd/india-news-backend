import asyncio
import os
import sys
from datetime import timedelta

# Ensure root of repo/backend is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from sqlalchemy import desc, or_, and_

from app.database import AsyncSessionLocal
from app.models import StoryCluster, Article, utc_now
from app.services.enrichment import enrich_cluster_with_ai

# Cap per run so a cron'd invocation can't burn an unbounded amount of API
# spend if a large backlog of unenriched clusters ever builds up. Only
# applies when no --since-days window is given — a windowed run is already
# bounded by the window itself, so it uses --limit (default None = no cap)
# instead of this.
DEFAULT_BATCH_LIMIT = 50


async def enrich_clusters(
    limit: int | None = DEFAULT_BATCH_LIMIT,
    force_all: bool = False,
    since_days: float | None = None,
) -> int:
    """Enrich story clusters, optionally windowed to the last `since_days`
    days (by last_updated_at) and/or forced to re-enrich regardless of
    current ai_enriched state. Returns the number of clusters processed.

    Singletons are included in AI enrichment now that it's a premium,
    customer-facing feature (see enrich_cluster_with_ai) — this script no
    longer needs to special-case them; the only remaining singleton-specific
    logic is in the *default* (non-forced, non-windowed) selection query
    below, which still can't use ai_enriched to detect "needs enrichment"
    for clusters that permanently fail the paid call.
    """
    async with AsyncSessionLocal() as session:
        # enrich_cluster_with_ai only ever reads title/snippet/source.name
        # (see app/services/enrichment.py) — load_only stops this from also
        # dragging every article's full scraped body over the wire, on a
        # remote (Supabase) Postgres where that's metered egress, every time
        # this runs (every 20 min via infra/news-enrich.timer).
        query = select(StoryCluster).options(
            selectinload(StoryCluster.articles).load_only(
                Article.title, Article.snippet, Article.source_id
            ).selectinload(Article.source)
        )

        if since_days is not None:
            cutoff = utc_now() - timedelta(days=since_days)
            query = query.where(StoryCluster.last_updated_at >= cutoff)

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
            query = query.where(
                or_(
                    StoryCluster.entities.is_(None),
                    StoryCluster.ai_enriched.is_(False),
                )
            )

        query = query.order_by(desc(StoryCluster.first_seen_at))
        if limit is not None:
            query = query.limit(limit)

        res = await session.execute(query)
        clusters = res.scalars().all()

        if not clusters:
            print("No clusters matched — nothing to do.")
            return 0

        total = len(clusters)
        print(f"Enriching {total} story cluster(s)...")
        for i, cluster in enumerate(clusters, 1):
            await enrich_cluster_with_ai(session, cluster)
            # Every 10 and on the last one, so `docker logs -f` gives a
            # live, ETA-able progress readout on long backfills instead of
            # having to eyeball/count individual "AI Enriched" lines.
            if i % 10 == 0 or i == total:
                print(f"[enrich] {i}/{total} clusters done ({i / total:.0%})")
        print(f"✅ Enriched {total} story clusters with entity tags & framing angles!")
        return total


if __name__ == "__main__":
    # --all forces re-enrichment of every cluster regardless of current state
    # (e.g. after a prompt change) — normal cron runs should NOT pass this.
    force_all = "--all" in sys.argv

    # --since-days N restricts to clusters touched in the last N days (by
    # last_updated_at). Omit for the full backlog. Since a windowed run is
    # already bounded by the window, it defaults to no --limit cap; pass
    # --limit N explicitly to still cap it.
    since_days = None
    limit = DEFAULT_BATCH_LIMIT
    for i, arg in enumerate(sys.argv):
        if arg == "--since-days" and i + 1 < len(sys.argv):
            since_days = float(sys.argv[i + 1])
            limit = None
        elif arg == "--limit" and i + 1 < len(sys.argv):
            limit = int(sys.argv[i + 1])

    asyncio.run(enrich_clusters(limit=limit, force_all=force_all, since_days=since_days))
