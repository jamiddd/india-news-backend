"""READ-ONLY dry run of the new clustering path against live data.

Verifies, before the new poller is ever allowed to write anything, that:

  1. The candidate query in poller._find_candidate_clusters actually executes
     against Postgres (it is built with .having(func.count() >= n) and a
     sorted IN list — constructs that only fail at execution time, not at
     import, and which no unit test can cover without a database).
  2. Candidate lookups are fast enough to run per-article in a poll cycle.
  3. The full match decision (candidate -> same-source guard -> shares_topic
     -> geo guard) produces sane merges on real headlines.

Writes nothing. Every statement it issues is a SELECT; safe to run against
production while the old poller is still live.

Usage:
    docker compose -f docker-compose.prod.yml run --rm app \
        python scripts/validate_clustering.py [--probes 300]
"""
import argparse
import asyncio
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select, text

from app.database import AsyncSessionLocal
from app.models import Article, Source
from app.services.dedup import shares_topic, title_tokens
from app.services.poller import (
    GEOGRAPHIC_CATEGORIES,
    MAX_CANDIDATE_CLUSTERS,
    CLUSTER_MATCH_WINDOW,
    _find_candidate_clusters,
)
from app.services.dedup import MIN_SHARED_TOKENS


async def main(probes: int) -> None:
    async with AsyncSessionLocal() as session:
        token_rows = (await session.execute(
            text("SELECT count(*) FROM cluster_tokens")
        )).scalar()
        distinct_clusters = (await session.execute(
            text("SELECT count(DISTINCT cluster_id) FROM cluster_tokens")
        )).scalar()
        print(f"cluster_tokens: {token_rows} rows over {distinct_clusters} clusters")
        print(f"window={CLUSTER_MATCH_WINDOW}  max_candidates={MAX_CANDIDATE_CLUSTERS}  "
              f"min_shared={MIN_SHARED_TOKENS}\n")

        source_categories = dict(
            (await session.execute(select(Source.id, Source.category))).all()
        )

        # Probe with the most recent articles: these are the closest stand-in
        # for what the poller will actually be handed next cycle.
        probe_rows = (await session.execute(
            select(Article.title, Article.source_id, Article.cluster_id)
            .order_by(Article.published_at.desc())
            .limit(probes)
        )).all()
        print(f"Probing {len(probe_rows)} recent articles...\n")

        timings = []
        matched = 0
        would_change = []

        for title, source_id, current_cluster_id in probe_rows:
            tokens = title_tokens(title or "")

            t0 = time.perf_counter()
            member_rows = await _find_candidate_clusters(
                session, tokens, MIN_SHARED_TOKENS
            )
            timings.append((time.perf_counter() - t0) * 1000)

            hit = None
            for cand_cluster_id, cand_title, cand_source_id in member_rows:
                if cand_source_id == source_id:
                    continue
                if not shares_topic(title, cand_title):
                    continue
                cat_a = source_categories.get(source_id)
                cat_b = source_categories.get(cand_source_id)
                if (
                    cat_a in GEOGRAPHIC_CATEGORIES
                    and cat_b in GEOGRAPHIC_CATEGORIES
                    and cat_a != cat_b
                ):
                    continue
                hit = (cand_cluster_id, cand_title)
                break

            if hit:
                matched += 1
                # A merge the OLD algorithm did not make is the whole point:
                # these are the stories that were being split into singletons.
                if hit[0] != current_cluster_id:
                    would_change.append((title, hit[1]))

        print(f"candidate lookup latency: "
              f"p50={statistics.median(timings):.1f}ms  "
              f"mean={statistics.mean(timings):.1f}ms  "
              f"max={max(timings):.1f}ms")
        print(f"probes that found a match: {matched}/{len(probe_rows)} "
              f"({matched / max(len(probe_rows), 1):.1%})")
        print(f"of those, merges the CURRENT clustering missed: {len(would_change)}\n")

        print("--- sample of newly-found merges (eyeball for false positives) ---")
        for probe_title, cand_title in would_change[:15]:
            print(f"\n  A: {probe_title[:100]}")
            print(f"  B: {cand_title[:100]}")

        if not would_change:
            print("(none — either the backfill is too sparse yet, or these "
                  "articles genuinely have no counterpart in the window)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--probes", type=int, default=300)
    args = ap.parse_args()
    asyncio.run(main(args.probes))
