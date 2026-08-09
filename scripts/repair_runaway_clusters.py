"""
One-off repair for clusters corrupted by the false-positive clustering bug
fixed alongside this script (see shares_topic() in app/services/dedup.py and
its call site in app/services/poller.py).

Symptom found in production: SimHash's Hamming-distance check alone (the old
matching condition) was too loose for short headlines — two completely
unrelated articles can coincidentally land within range just by sharing a
few common (stopword-heavy) tokens. Once a cluster picked up one bad match,
every fresh addition kept bumping its last_updated_at, keeping it inside the
"100 most recently updated" comparison window indefinitely — a self-
reinforcing "black hole" that kept absorbing unrelated stories for weeks. A
real example: a cluster whose displayed headline was "Yash breaks silence on
'Toxic' delay..." had 21 "outlets" attached, actually unrelated pieces about
a Japan typhoon, herpes, a stablecoin launch, UPSC toppers, and more.

What this script does, per cluster with article_count >= 2:
  1. Re-check every member article against the cluster's representative
     article using the NEW rule (shares_topic — actual content-word
     overlap), not just the old Hamming-distance-only rule.
  2. Any article that fails the new check is detached: it becomes its own
     new singleton cluster (never deleted — no data loss, just re-filed).
  3. If detaching leaves the original cluster with 1 article, its
     entities/topics/framing_comparison (products of the old, contaminated
     membership) are cleared so a future enrichment pass regenerates them
     honestly instead of leaving stale cross-story analysis attached to a
     single-article cluster.

Read-only by default (--apply required to write). Run --apply once after
deploying the dedup.py/poller.py fix.
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import AsyncSessionLocal
from app.models import Article, StoryCluster, utc_now
from app.services.dedup import shares_topic


async def main(apply: bool, min_article_count: int):
    async with AsyncSessionLocal() as session:
        query = (
            select(StoryCluster)
            .where(StoryCluster.article_count >= min_article_count)
            .options(selectinload(StoryCluster.articles))
        )
        clusters = (await session.execute(query)).scalars().all()

        print(f"Scanning {len(clusters)} clusters with article_count >= {min_article_count}...")

        total_detached = 0
        flagged_clusters = 0

        for cluster in clusters:
            articles = list(cluster.articles)
            rep = next((a for a in articles if a.id == cluster.representative_article_id), None)
            if rep is None:
                # Representative got deleted/unlinked at some point; can't
                # judge membership without it — skip rather than guess.
                continue

            misfits = [
                a for a in articles
                if a.id != rep.id and not shares_topic(rep.title, rep.snippet, a.title, a.snippet)
            ]

            if not misfits:
                continue

            flagged_clusters += 1
            print(f"\nCluster {cluster.id}: \"{cluster.headline[:80]}\"")
            print(f"  representative: \"{rep.title[:80]}\"")
            print(f"  {len(misfits)}/{len(articles)} members don't share topic with it:")
            for a in misfits:
                print(f"    - [{a.id}] {a.title[:80]}")

            if not apply:
                continue

            for a in misfits:
                new_cluster = StoryCluster(
                    headline=a.title,
                    summary=a.snippet,
                    article_count=1,
                    first_seen_at=a.published_at,
                    last_updated_at=a.published_at,
                )
                session.add(new_cluster)
                await session.flush()
                new_cluster.representative_article_id = a.id
                a.cluster_id = new_cluster.id
                total_detached += 1

            cluster.article_count = len(articles) - len(misfits)
            if cluster.article_count <= 1:
                cluster.entities = None
                cluster.topics = None
                cluster.framing_comparison = None
            cluster.last_updated_at = utc_now()

        if apply:
            await session.commit()
            print(f"\nDone: {total_detached} article(s) detached out of {flagged_clusters} corrupted cluster(s).")
        else:
            print(f"\nDry run: {flagged_clusters} cluster(s) would be affected. Re-run with --apply to fix.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write changes (default: dry run / report only)")
    parser.add_argument("--min-article-count", type=int, default=2, help="Only scan clusters with at least this many articles")
    args = parser.parse_args()
    asyncio.run(main(apply=args.apply, min_article_count=args.min_article_count))
