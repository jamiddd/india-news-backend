import asyncio
import os
import sys
from datetime import timedelta

# Ensure root of repo/backend is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from sqlalchemy import desc, or_, and_

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import StoryCluster, Article, utc_now
from app.services.enrichment import enrich_cluster_with_ai
from app.services.enrichment_batch import (
    reconcile_open_batches,
    submit_refinement_batch,
)

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

    Only clusters with at least FEED_MIN_DISTINCT_SOURCES distinct outlets
    are selected. Singletons keep their original RSS headline and are never
    sent to an LLM.

    This reverses the earlier decision to enrich singletons too. That was
    made when enrichment was framed as a premium feature owed to every
    story; the measurement that changed it is that 97.5% of clusters are a
    single RSS item, and paraphrasing each one's 250-char snippet cost
    ~$238/month to restate text already on screen. Demand drops from
    ~4,800/day to ~350/day. A singleton that later earns a second outlet is
    enriched then — the poller clears ai_enriched at that crossing — so this
    defers enrichment rather than denying it. See
    docs/multi-source-feed-plan.md §5.C.

    The gate applies to --all and --since-days runs too: a forced backfill
    is exactly the run where enriching every singleton would be most
    expensive.
    """
    async with AsyncSessionLocal() as session:
        # Before submitting anything new, collect whatever finished since the
        # last tick. Doing this first means a batch's results land as soon as
        # the next run starts, rather than a tick later.
        reconciled = await reconcile_open_batches(session)
        if reconciled["checked"]:
            print(
                f"[enrich] batches: {reconciled['checked']} open, "
                f"{reconciled['ended']} ended, "
                f"{reconciled['succeeded']} applied, "
                f"{reconciled['errored']} failed"
            )

        # enrich_cluster_with_ai reads title/content/snippet/source.name (see
        # app/services/enrichment.py) — load_only keeps this from also
        # dragging columns nobody in that path reads over the wire, on a
        # remote (Supabase) Postgres where that's metered egress, every time
        # this runs (every 20 min via infra/news-enrich.timer).
        #
        # Article.content is now part of that set and it is the big column.
        # It has to be loaded here: leaving it out does not save the egress,
        # it just moves it to a lazy load per article inside the enrichment
        # call — which on an AsyncSession raises rather than silently
        # emitting the query. The gate below is what actually bounds the
        # cost, by cutting the row count ~14x.
        query = select(StoryCluster).options(
            selectinload(StoryCluster.articles).load_only(
                Article.title, Article.content, Article.snippet, Article.source_id
            ).selectinload(Article.source)
        )

        # The multi-source gate (§5.C). Applied before every other filter and
        # in every mode, forced runs included.
        query = query.where(
            StoryCluster.distinct_source_count >= settings.FEED_MIN_DISTINCT_SOURCES
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

        # Split the work by what is actually waiting on it (§5.G).
        #
        # A cluster with no successful pass yet is a story entering the feed
        # with nothing but its raw RSS headline to show — that goes
        # synchronously. One that has been enriched before and merely gained
        # another outlet is a refinement nobody is waiting on, so it goes to
        # the Batch API at half price. last_enriched_at is the right test and
        # ai_enriched is not: the poller resets ai_enriched for both cases.
        first_pass = [c for c in clusters if c.last_enriched_at is None]
        refinements = [c for c in clusters if c.last_enriched_at is not None]

        total = len(first_pass)
        if total:
            print(f"Enriching {total} story cluster(s) synchronously...")
            for i, cluster in enumerate(first_pass, 1):
                await enrich_cluster_with_ai(session, cluster)
                # Every 10 and on the last one, so `docker logs -f` gives a
                # live, ETA-able progress readout on long backfills instead of
                # having to eyeball/count individual "AI Enriched" lines.
                if i % 10 == 0 or i == total:
                    print(f"[enrich] {i}/{total} clusters done ({i / total:.0%})")

        if refinements:
            batch_id = await submit_refinement_batch(session, refinements)
            print(
                f"[enrich] queued {len(refinements)} refinement(s) "
                f"to batch {batch_id}"
            )

        print(
            f"✅ Enriched {total} cluster(s) now, "
            f"{len(refinements)} queued for batch."
        )
        return total + len(refinements)


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
