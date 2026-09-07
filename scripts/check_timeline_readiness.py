"""Read-only data check for the story-timeline rework (see
backend/docs/story-graph-design.md for the abandoned first attempt, and the
2026-09-07 session for why we're retrying now: clustering itself was the
blocker then — 98.5% singletons — and is fixed as of the 2026-09-03 retune).

This answers one question before any chaining code is written: is the
*current* cluster population healthy enough to chain, and how much would
a fetch like this cost in Supabase egress?

No writes. No article bodies loaded — only story_clusters/entity_stats
columns, same lean-column convention as app/services/related_stories.py, to
keep egress low on a metered connection (see backend/docs/
clustering-rework-handoff.md §5). Prints byte-count estimates so a real
number replaces the guess made when this was scoped.

Usage (on a host with DATABASE_URL pointing at prod — i.e. run via
`docker exec` on the server, not locally; see backend-deploy-workflow):

    docker exec news_backend_prod python3 scripts/check_timeline_readiness.py --days 30
"""
import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import text  # noqa: E402

from app.database import async_session_maker  # noqa: E402


async def main(days: int) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    async with async_session_maker() as session:
        # 1. Volume + corroboration shape over the window.
        result = await session.execute(
            text(
                """
                SELECT id, headline, entities, first_seen_at, last_updated_at,
                       distinct_source_count
                FROM story_clusters
                WHERE first_seen_at >= :cutoff
                """
            ),
            {"cutoff": cutoff},
        )
        rows = result.mappings().all()

        total = len(rows)
        multi_source = sum(1 for r in rows if (r["distinct_source_count"] or 1) >= 2)
        backdrop_present = 0
        entities_present = 0
        entity_counts = Counter()
        raw_bytes = 0

        for r in rows:
            entities = r["entities"] or {}
            raw_bytes += len(json.dumps(dict(r), default=str).encode("utf-8"))
            if entities:
                entities_present += 1
            if entities.get("backdrop"):
                backdrop_present += 1
            for field_name in ("persons", "organizations", "locations"):
                for name in entities.get(field_name) or []:
                    entity_counts[name.strip().lower()] += 1

        # 2. entity_stats maturity — feeds the genericity check any chain
        # logic reuses from Round 3/5 (Nodes 2-4).
        stats_result = await session.execute(text("SELECT count(*) FROM entity_stats"))
        entity_stats_rows = stats_result.scalar_one()

        # 3. Candidate density: how many clusters would even have >=1
        # other cluster sharing an entity — the precondition for any
        # continuation edge to exist at all. Cheap approximation: entities
        # appearing on 2+ clusters in-window.
        shared_entities = sum(1 for _, c in entity_counts.items() if c >= 2)

    print(f"--- story-timeline readiness check ({days}-day window) ---")
    print(f"clusters in window:              {total}")
    print(f"  multi-source (>=2 outlets):     {multi_source} ({multi_source / total:.1%})" if total else "  multi-source: n/a")
    print(f"  with any entities extracted:    {entities_present} ({entities_present / total:.1%})" if total else "")
    print(f"  with entities.backdrop tagged:  {backdrop_present} ({backdrop_present / total:.1%})" if total else "")
    print(f"distinct entity keys seen:        {len(entity_counts)}")
    print(f"entity keys shared by >=2 clusters (candidate-edge fuel): {shared_entities}")
    print(f"entity_stats rows (genericity-check maturity): {entity_stats_rows}")
    print()
    print(f"approx fetch size this window:   {raw_bytes / 1024:.1f} KB "
          f"({raw_bytes / 1024 / 1024:.3f} MB) — no article bodies included")
    print()
    top = entity_counts.most_common(15)
    print("top entities by cluster mentions (sanity check — expect people/orgs, not junk):")
    for name, c in top:
        print(f"  {c:>4}  {name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()
    asyncio.run(main(args.days))
