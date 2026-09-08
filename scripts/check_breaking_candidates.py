"""Read-only data check for the "Breaking" slot feature (velocity-triggered
promotion of sudden, high-source-count stories — Nepal-flood/Delhi-collapse
shape — into a dedicated timeline UI). See the 2026-09-08 planning session.

This answers the two questions that gate the whole feature before any app
code is written:

1. How many clusters/day actually cross a velocity threshold (>=N distinct
   sources within H hours of becoming multi-source)? If it's 1-5/day the
   proposed two-slot cap is right; if it's 20+/day the threshold needs to
   move before anything is built.

2. For a given cluster, do its articles actually contain a *developing*
   story (new facts arriving over time) or just N outlets echoing one fact?
   `--cluster <id>` dumps articles in published_at order — the exact input
   shape the eventual LLM prompt would see — so this can be judged by eye
   for free before spending anything on a model call.

No writes. distinct_source_count is a live counter that can't answer "when
did this cluster cross N sources" historically, so gate volume is
reconstructed from articles: for each cluster, the first-seen time of each
distinct source, ranked, giving the timestamp its Nth distinct source
appeared.

Usage (on a host with DATABASE_URL pointing at prod — i.e. run via
`docker exec` on the server, not locally; see backend-deploy-workflow):

    docker exec news_backend_prod python3 scripts/check_breaking_candidates.py --days 30
    docker exec news_backend_prod python3 scripts/check_breaking_candidates.py --days 30 --min-sources 20 --window-hours 24
    docker exec news_backend_prod python3 scripts/check_breaking_candidates.py --cluster 12345
"""
import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import text  # noqa: E402

from app.database import AsyncSessionLocal  # noqa: E402


# Every distinct source's first article in each cluster, then ranked by time
# per cluster — row N is the moment the cluster acquired its Nth distinct
# source. Shared by both the gate-volume query and the candidate listing so
# the two can't drift on what "reached N sources" means.
NTH_SOURCE_CTE = """
    WITH first_hit AS (
        SELECT a.cluster_id, a.source_id, MIN(a.published_at) AS t
        FROM articles a
        WHERE a.cluster_id IS NOT NULL
        GROUP BY 1, 2
    ),
    ranked AS (
        SELECT cluster_id, t,
               ROW_NUMBER() OVER (PARTITION BY cluster_id ORDER BY t) AS rn
        FROM first_hit
    ),
    nth_source AS (
        SELECT cluster_id, t AS t_n
        FROM ranked
        WHERE rn = :min_sources
    )
"""


async def gate_volume(session, cutoff, min_sources: int, window_hours: int) -> None:
    result = await session.execute(
        text(
            NTH_SOURCE_CTE
            + """
            SELECT date_trunc('day', n.t_n)::date AS day,
                   count(*) FILTER (
                       WHERE n.t_n - COALESCE(c.became_multi_source_at, c.first_seen_at)
                             < make_interval(hours => :window_hours)
                   ) AS passes_velocity_gate,
                   count(*) AS reaches_n_any_speed
            FROM nth_source n
            JOIN story_clusters c ON c.id = n.cluster_id
            WHERE n.t_n >= :cutoff
            GROUP BY 1
            ORDER BY 1
            """
        ),
        {"cutoff": cutoff, "min_sources": min_sources, "window_hours": window_hours},
    )
    rows = result.mappings().all()

    print(f"--- breaking-slot gate volume (>= {min_sources} sources, "
          f"< {window_hours}h since multi-source) ---")
    if not rows:
        print("  (no clusters reached the source threshold in this window)")
        return

    total_gate = sum(r["passes_velocity_gate"] for r in rows)
    total_any = sum(r["reaches_n_any_speed"] for r in rows)
    print(f"  {'day':<12} {'velocity-gate':>14} {'any-speed':>10}")
    for r in rows:
        print(f"  {str(r['day']):<12} {r['passes_velocity_gate']:>14} {r['reaches_n_any_speed']:>10}")
    days = len(rows)
    print()
    print(f"  totals: {total_gate} velocity-gate / {total_any} any-speed over {days} day(s)"
          f" ({total_gate / days:.1f}/day velocity-gate avg)")
    print("  (gap between the two columns = how much the velocity term filters"
          " out slow-burn stories that reach N sources over days, not hours)")


async def candidate_listing(session, cutoff, min_sources: int, window_hours: int, limit: int) -> None:
    result = await session.execute(
        text(
            NTH_SOURCE_CTE
            + """
            SELECT c.id, c.headline, c.distinct_source_count,
                   round(EXTRACT(EPOCH FROM (
                       n.t_n - COALESCE(c.became_multi_source_at, c.first_seen_at)
                   )) / 3600.0, 1) AS hours_to_n
            FROM nth_source n
            JOIN story_clusters c ON c.id = n.cluster_id
            WHERE n.t_n >= :cutoff
              AND n.t_n - COALESCE(c.became_multi_source_at, c.first_seen_at)
                  < make_interval(hours => :window_hours)
            ORDER BY hours_to_n ASC
            LIMIT :limit
            """
        ),
        {"cutoff": cutoff, "min_sources": min_sources, "window_hours": window_hours, "limit": limit},
    )
    rows = result.mappings().all()

    print()
    print(f"--- fastest candidates to reach {min_sources} sources (top {limit}) ---")
    if not rows:
        print("  (none)")
        return
    for r in rows:
        headline = (r["headline"] or "").strip()
        if len(headline) > 80:
            headline = headline[:77] + "..."
        print(f"  id={r['id']:<8} sources={r['distinct_source_count']:<4} "
              f"hours_to_{min_sources}={r['hours_to_n']:<6} {headline}")
    print()
    print("  pick an id and re-run with --cluster <id> to inspect its articles.")


async def dump_cluster_articles(session, cluster_id: int) -> None:
    cluster_result = await session.execute(
        text(
            """
            SELECT id, headline, distinct_source_count, first_seen_at,
                   became_multi_source_at, last_updated_at
            FROM story_clusters
            WHERE id = :cluster_id
            """
        ),
        {"cluster_id": cluster_id},
    )
    cluster = cluster_result.mappings().first()
    if cluster is None:
        print(f"cluster {cluster_id} not found")
        return

    print(f"--- cluster {cluster_id} ---")
    print(f"headline: {cluster['headline']}")
    print(f"distinct_source_count: {cluster['distinct_source_count']}")
    print(f"first_seen_at: {cluster['first_seen_at']}   "
          f"became_multi_source_at: {cluster['became_multi_source_at']}   "
          f"last_updated_at: {cluster['last_updated_at']}")
    print()

    articles_result = await session.execute(
        text(
            """
            SELECT a.published_at, s.name AS source_name, a.title
            FROM articles a
            JOIN sources s ON s.id = a.source_id
            WHERE a.cluster_id = :cluster_id
            ORDER BY a.published_at ASC
            """
        ),
        {"cluster_id": cluster_id},
    )
    articles = articles_result.mappings().all()

    print(f"articles in publish order ({len(articles)} total) "
          "— this is the exact input shape an LLM developing-vs-echo pass would see:")
    for a in articles:
        title = (a["title"] or "").strip()
        print(f"  {a['published_at']}  [{a['source_name']:<20}]  {title}")


async def main(days: int, min_sources: int, window_hours: int, limit: int, cluster_id: int | None) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    async with AsyncSessionLocal() as session:
        if cluster_id is not None:
            await dump_cluster_articles(session, cluster_id)
            return

        await gate_volume(session, cutoff, min_sources, window_hours)
        await candidate_listing(session, cutoff, min_sources, window_hours, limit)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30, help="lookback window for the gate-volume/candidate reports")
    parser.add_argument("--min-sources", type=int, default=20, help="distinct-source threshold for the velocity gate")
    parser.add_argument("--window-hours", type=int, default=24, help="max hours since became_multi_source_at to count as 'sudden'")
    parser.add_argument("--limit", type=int, default=25, help="max rows in the candidate listing")
    parser.add_argument("--cluster", type=int, default=None, help="dump this cluster's articles in publish order instead of the reports")
    args = parser.parse_args()
    asyncio.run(main(args.days, args.min_sources, args.window_hours, args.limit, args.cluster))
