"""
Read-only experiment for "story graph mode" (see the "Feed ranking redesign"
design doc/memory): tests whether a cheap entity-overlap heuristic alone is
strong enough to detect story-to-story continuation ("this cluster is a
follow-up/development of that earlier cluster"), before deciding whether an
LLM confirmation pass is needed on top of it.

No writes, no new tables, no LLM calls. Scores candidate (earlier, later)
cluster pairs within a time window using canonicalized shared entities
(app.services.entity_graph.canonicalize_entity), weighted by rarity, and
prints them for manual review.

Rarity is computed as inverse document frequency over the loaded cluster
set itself (log(N / df) for each entity_key), not from entity_stats —
entity_stats only holds ~700 rows (populated from a 30-min recompute
lookback, see poller.py) and is far too sparse to weight ~10k clusters'
worth of distinct entities; nearly everything fell back to a default
weight and the "rarity" signal was flat. In-set IDF has no such gap.

A minimum shared-entity count (--min-shared, default 2) also applies:
a single shared entity (e.g. both stories merely mention "Apple") produced
massive false-positive fan-out in the first pass — two clusters need to
agree on at least 2 entities to be considered a candidate pair at all.

A minimum time gap (--min-gap-hours, default 0) is available to separate two
different populations that both show up in the scored pairs: same-day
near-duplicate coverage of one event (minutes-to-hours apart — arguably a
missed dedup, not a "story so far" edge) vs. genuine multi-day narrative
development (a legal case, a public feud, a follow-up report days later).
Raise it to focus on the latter.

Chain building (--chains): streaming, root-anchored assignment, one level
up from poller.py's "assign article to existing cluster, else start a new
one" pattern. Clusters are walked in time order; each is scored against
every active thread's ROOT entity set (not its most recent member) and
joins the best-scoring thread above --threshold, or starts a new thread of
its own. Anchoring to the root (rather than the previous hop) guards
against topic drift — an earlier connected-components + best-predecessor
version let chains wander (e.g. "Nilgiris water stress" -> "elephant
deaths" -> "man-eating tiger" -> "leopard poaching", each hop locally
plausible via a shared location entity but the chain as a whole not one
story) because each hop only had to agree with its neighbor, not with what
the story was originally about.

Generic entities (--max-df-ratio, default 0.015) are pruned before matching
entirely, not just down-weighted — IDF alone doesn't stop e.g. "india" +
"government_of_india" or "tamil_nadu" + "tamil_nadu_government" from
satisfying --min-shared and chaining together dozens of unrelated stories
that just happen to share the same country/party/state, since --min-shared
counts entities, not weight. Same idea as dropping stopwords before TF-IDF.

Usage (inside the app container, so DATABASE_URL is set):
    python3 scripts/experiment_story_edges.py
    python3 scripts/experiment_story_edges.py --days 60 --threshold 0.15 --limit 100
    python3 scripts/experiment_story_edges.py --min-shared 3 --csv /tmp/story_edges.csv
    python3 scripts/experiment_story_edges.py --min-gap-hours 12 --chains
"""
import argparse
import asyncio
import csv
import math
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Set, Tuple

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import text

from app.database import engine
from app.services.entity_graph import canonicalize_entity


@dataclass
class Cluster:
    id: int
    headline: str
    first_seen_at: datetime
    last_updated_at: datetime
    entity_keys: Set[str] = field(default_factory=set)


@dataclass
class CandidatePair:
    earlier: Cluster
    later: Cluster
    score: float
    shared: List[Tuple[str, float]]  # (entity_key, weight)


async def load_clusters(conn, days: int) -> List[Cluster]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    result = await conn.execute(
        text(
            """
            SELECT id, headline, entities, first_seen_at, last_updated_at
            FROM story_clusters
            WHERE first_seen_at >= :cutoff
            ORDER BY first_seen_at ASC
            """
        ),
        {"cutoff": cutoff},
    )
    clusters = []
    for row in result:
        entities = row.entities or {}
        entity_keys: Set[str] = set()
        for entity_type, field_name in (
            ("person", "persons"),
            ("organization", "organizations"),
            ("location", "locations"),
        ):
            for raw_name in entities.get(field_name, []) or []:
                key = canonicalize_entity(raw_name, entity_type)
                if key:
                    entity_keys.add(key)
        clusters.append(
            Cluster(
                id=row.id,
                headline=row.headline or "",
                first_seen_at=row.first_seen_at,
                last_updated_at=row.last_updated_at,
                entity_keys=entity_keys,
            )
        )
    return clusters


def compute_idf_weights(clusters: List[Cluster]) -> Dict[str, float]:
    """log(N / df) per entity_key, df = number of loaded clusters mentioning it."""
    doc_freq: Dict[str, int] = defaultdict(int)
    for cluster in clusters:
        for key in cluster.entity_keys:
            doc_freq[key] += 1
    n = len(clusters)
    # +1 smoothing keeps weight positive and finite even if df == n.
    return {key: math.log(n / df) + 1.0 for key, df in doc_freq.items()}


def prune_generic_entities(clusters: List[Cluster], max_df_ratio: float) -> None:
    """
    Drop entity keys that appear in more than max_df_ratio of all loaded
    clusters, in place. IDF weighting alone doesn't stop generic
    country/party/state entities (india, bjp, congress, tamil_nadu,
    tamil_nadu_government) from chaining unrelated stories together — two
    such entities together still clear --min-shared even at low individual
    weight, since min-shared counts entities, not weight. This is the same
    fix as dropping stopwords before TF-IDF: entities this common carry no
    story-identifying signal, so they're removed from matching entirely
    rather than merely down-weighted.
    """
    doc_freq: Dict[str, int] = defaultdict(int)
    for cluster in clusters:
        for key in cluster.entity_keys:
            doc_freq[key] += 1
    n = len(clusters)
    max_df = max_df_ratio * n
    generic = {key for key, df in doc_freq.items() if df > max_df}
    for cluster in clusters:
        cluster.entity_keys -= generic


def score_pairs(
    clusters: List[Cluster], weights: Dict[str, float], days: int, min_shared: int,
    min_gap_hours: float = 0.0,
) -> List[CandidatePair]:
    # Inverted index: entity_key -> cluster ids that mention it, so we only
    # ever compare clusters that share at least one entity.
    index: Dict[str, List[Cluster]] = defaultdict(list)
    for cluster in clusters:
        for key in cluster.entity_keys:
            index[key].append(cluster)

    max_gap = timedelta(days=days)
    min_gap = timedelta(hours=min_gap_hours)
    seen_pairs: Set[Tuple[int, int]] = set()
    pairs: List[CandidatePair] = []

    for key, members in index.items():
        if len(members) < 2:
            continue
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = members[i], members[j]
                if a.first_seen_at == b.first_seen_at:
                    continue
                earlier, later = (a, b) if a.first_seen_at < b.first_seen_at else (b, a)
                pair_key = (earlier.id, later.id)
                if pair_key in seen_pairs:
                    continue
                gap = later.first_seen_at - earlier.first_seen_at
                if gap > max_gap or gap < min_gap:
                    continue
                seen_pairs.add(pair_key)

                shared_keys = earlier.entity_keys & later.entity_keys
                if len(shared_keys) < min_shared:
                    continue

                shared_weighted = []
                total_weight = 0.0
                for shared_key in shared_keys:
                    weight = weights.get(shared_key, 1.0)
                    shared_weighted.append((shared_key, weight))
                    total_weight += weight

                norm = math.sqrt(len(earlier.entity_keys) * len(later.entity_keys))
                score = total_weight / norm if norm > 0 else 0.0

                shared_weighted.sort(key=lambda pair: pair[1], reverse=True)
                pairs.append(CandidatePair(earlier, later, score, shared_weighted))

    pairs.sort(key=lambda p: p.score, reverse=True)
    return pairs


def print_pairs(pairs: List[CandidatePair], limit: int) -> None:
    print(f"\nTop {min(limit, len(pairs))} of {len(pairs)} candidate pairs above threshold:\n")
    for pair in pairs[:limit]:
        gap = pair.later.first_seen_at - pair.earlier.first_seen_at
        shared_names = ", ".join(k.split(":", 1)[1] for k, _ in pair.shared[:6])
        print(f"score={pair.score:.3f}  gap={gap}")
        print(f"  A #{pair.earlier.id:6d} {pair.earlier.headline[:90]}")
        print(f"  B #{pair.later.id:6d} {pair.later.headline[:90]}")
        print(f"  shared: {shared_names}")
        print()


@dataclass
class AnchoredThread:
    root: Cluster
    root_entities: Set[str]
    members: List[Tuple[Cluster, float, Set[str]]] = field(default_factory=list)  # (cluster, score, shared)
    last_seen: Optional[datetime] = None


def build_anchored_chains(
    clusters: List[Cluster], weights: Dict[str, float], min_shared: int,
    days: int, threshold: float,
) -> List[AnchoredThread]:
    """
    Streaming, root-anchored chain assignment — same shape as poller.py's
    "assign article to existing cluster, else start a new one" pattern, one
    level up (story-to-story instead of article-to-story).

    build_chains()'s connected-component approach let chains drift: each
    hop only had to match its immediate predecessor, so a long chain could
    wander away from its original topic one locally-plausible link at a
    time (e.g. Nilgiris water stress -> elephant deaths -> man-eating tiger
    -> leopard poaching, "linked" only by a shared location entity at each
    step). Here every candidate is scored against the thread's ROOT entity
    set, not its last member, so a chain can only grow as long as it keeps
    overlapping with what the story was originally about.

    Deliberately no --min-gap-hours gate here (unlike score_pairs): an
    earlier version rejected a candidate for landing too soon after the
    thread's last addition, which split single bursts of same-story
    coverage (several outlets within an hour) into orphaned parallel
    chains — the first burst article joined fine, the second got rejected
    for being "too soon" and spun off its own thread, which then attracted
    later real follow-ups that should have stayed on the original chain.
    Membership is purely score-based; min_gap_hours is applied only for
    display (see print_anchored_chains), tagging bursty hops rather than
    excluding them.
    """
    clusters_sorted = sorted(clusters, key=lambda c: c.first_seen_at)
    max_gap = timedelta(days=days)
    threads: List[AnchoredThread] = []

    for cluster in clusters_sorted:
        best_thread = None
        best_score = 0.0
        best_shared: Set[str] = set()

        for thread in threads:
            if cluster.first_seen_at - thread.root.first_seen_at > max_gap:
                continue
            shared = cluster.entity_keys & thread.root_entities
            if len(shared) < min_shared:
                continue
            total_weight = sum(weights.get(k, 1.0) for k in shared)
            norm = math.sqrt(len(cluster.entity_keys) * len(thread.root_entities))
            score = total_weight / norm if norm > 0 else 0.0
            if score >= threshold and score > best_score:
                best_score, best_thread, best_shared = score, thread, shared

        if best_thread is not None:
            best_thread.members.append((cluster, best_score, best_shared))
            best_thread.last_seen = cluster.first_seen_at
        else:
            threads.append(AnchoredThread(
                root=cluster, root_entities=cluster.entity_keys, last_seen=cluster.first_seen_at,
            ))

    return threads


def print_anchored_chains(threads: List[AnchoredThread], limit: int, min_gap_hours: float) -> None:
    min_gap = timedelta(hours=min_gap_hours)
    multi = [t for t in threads if t.members]
    multi.sort(key=lambda t: len(t.members), reverse=True)
    print(f"\n{len(multi)} root-anchored chains (of {len(threads)} total roots), "
          f"largest first — showing up to {limit}:\n")
    for thread in multi[:limit]:
        print(f"=== chain of {len(thread.members) + 1} clusters, root #{thread.root.id} ===")
        when = thread.root.first_seen_at.strftime("%Y-%m-%d %H:%M")
        print(f"  [{when}] #{thread.root.id:6d} {thread.root.headline[:90]}")
        previous_seen = thread.root.first_seen_at
        for cluster, score, shared in sorted(thread.members, key=lambda m: m[0].first_seen_at):
            when = cluster.first_seen_at.strftime("%Y-%m-%d %H:%M")
            shared_names = ", ".join(k.split(":", 1)[1] for k in list(shared)[:4])
            burst_tag = " [same burst]" if cluster.first_seen_at - previous_seen < min_gap else ""
            print(f"  [{when}] #{cluster.id:6d} {cluster.headline[:90]}{burst_tag}")
            print(f"      └─ vs root, score={score:.2f}, shared: {shared_names}")
            previous_seen = cluster.first_seen_at
        print()


def write_csv(pairs: List[CandidatePair], path: str) -> None:
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["score", "gap_days", "cluster_a_id", "cluster_a_headline",
             "cluster_b_id", "cluster_b_headline", "shared_entities"]
        )
        for pair in pairs:
            gap_days = (pair.later.first_seen_at - pair.earlier.first_seen_at).total_seconds() / 86400
            shared_names = "; ".join(k.split(":", 1)[1] for k, _ in pair.shared)
            writer.writerow(
                [f"{pair.score:.4f}", f"{gap_days:.2f}", pair.earlier.id, pair.earlier.headline,
                 pair.later.id, pair.later.headline, shared_names]
            )
    print(f"Wrote {len(pairs)} candidate pairs to {path}")


async def main(
    days: int, threshold: float, limit: int, csv_path: Optional[str], min_shared: int,
    min_gap_hours: float, chains: bool, max_df_ratio: float,
) -> None:
    async with engine.begin() as conn:
        clusters = await load_clusters(conn, days)

    if max_df_ratio < 1.0:
        prune_generic_entities(clusters, max_df_ratio)

    weights = compute_idf_weights(clusters)
    print(f"Loaded {len(clusters)} clusters from the last {days} days "
          f"and {len(weights)} distinct entity keys (in-set IDF weighting).")

    if chains:
        threads = build_anchored_chains(clusters, weights, min_shared, days, threshold)
        print_anchored_chains(threads, limit, min_gap_hours)
        return

    pairs = score_pairs(clusters, weights, days, min_shared, min_gap_hours)
    pairs = [p for p in pairs if p.score >= threshold]

    if csv_path:
        write_csv(pairs, csv_path)
    else:
        print_pairs(pairs, limit)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=60, help="Lookback window in days (default: 60)")
    parser.add_argument("--threshold", type=float, default=0.1, help="Minimum score to print (default: 0.1)")
    parser.add_argument("--limit", type=int, default=100, help="Max pairs to print to stdout (default: 100)")
    parser.add_argument("--csv", type=str, default=None, help="Write all pairs above threshold to this CSV path instead of stdout")
    parser.add_argument("--min-shared", type=int, default=2, help="Minimum shared entities to count as a candidate pair (default: 2)")
    parser.add_argument("--min-gap-hours", type=float, default=0.0, help="Flat-pair mode: minimum gap to count as a candidate pair at all. Chains mode: hops closer together than this are tagged '[same burst]' rather than excluded (default: 0)")
    parser.add_argument("--chains", action="store_true", help="Group candidate pairs into connected-component chains (one predecessor per cluster) instead of printing a flat pair list")
    parser.add_argument("--max-df-ratio", type=float, default=0.015, help="Drop entities appearing in more than this fraction of loaded clusters before matching (default: 0.015, i.e. ~1.5%%); pass 1.0 to disable")
    args = parser.parse_args()

    asyncio.run(main(args.days, args.threshold, args.limit, args.csv, args.min_shared, args.min_gap_hours, args.chains, args.max_df_ratio))
