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

Chain building: candidate pairs above --threshold form edges; connected
components (union-find) group clusters that are plausibly "the same story."
Within each component, each cluster keeps only its single highest-scoring
earlier predecessor, collapsing what would otherwise be a dense blob (e.g.
every Apple story linked to every other Apple story) into an actual
chronological chain/tree per component — this is what a "story so far"
timeline would walk.

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


class UnionFind:
    def __init__(self) -> None:
        self.parent: Dict[int, int] = {}

    def find(self, x: int) -> int:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def build_chains(pairs: List[CandidatePair]) -> Dict[int, List[Tuple[Cluster, Optional[CandidatePair]]]]:
    """
    Group clusters into connected components (union-find over candidate
    pairs), then within each component keep only each cluster's single
    highest-scoring earlier predecessor edge — collapsing a dense blob of
    pairwise links into an actual chronological chain/tree.

    Returns component_root_id -> [(cluster, edge_from_predecessor_or_None), ...]
    sorted by first_seen_at, for printing as a timeline.
    """
    uf = UnionFind()
    clusters_by_id: Dict[int, Cluster] = {}
    for pair in pairs:
        clusters_by_id[pair.earlier.id] = pair.earlier
        clusters_by_id[pair.later.id] = pair.later
        uf.union(pair.earlier.id, pair.later.id)

    best_predecessor: Dict[int, CandidatePair] = {}
    for pair in pairs:
        existing = best_predecessor.get(pair.later.id)
        if existing is None or pair.score > existing.score:
            best_predecessor[pair.later.id] = pair

    components: Dict[int, List[int]] = defaultdict(list)
    for cluster_id in clusters_by_id:
        components[uf.find(cluster_id)].append(cluster_id)

    chains: Dict[int, List[Tuple[Cluster, Optional[CandidatePair]]]] = {}
    for root, member_ids in components.items():
        members = sorted(
            (clusters_by_id[cid] for cid in member_ids), key=lambda c: c.first_seen_at
        )
        chains[root] = [(c, best_predecessor.get(c.id)) for c in members]
    return chains


def print_chains(chains: Dict[int, List[Tuple[Cluster, Optional[CandidatePair]]]], limit: int) -> None:
    ordered = sorted(chains.values(), key=len, reverse=True)
    print(f"\n{len(ordered)} story chains (connected components), largest first "
          f"— showing up to {limit}:\n")
    for chain in ordered[:limit]:
        print(f"=== chain of {len(chain)} clusters ===")
        for cluster, edge in chain:
            when = cluster.first_seen_at.strftime("%Y-%m-%d %H:%M")
            if edge is None:
                print(f"  [{when}] #{cluster.id:6d} {cluster.headline[:90]}")
            else:
                shared_names = ", ".join(k.split(":", 1)[1] for k, _ in edge.shared[:4])
                print(f"  [{when}] #{cluster.id:6d} {cluster.headline[:90]}")
                print(f"      └─ from #{edge.earlier.id} (score={edge.score:.2f}, shared: {shared_names})")
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
    min_gap_hours: float, chains: bool,
) -> None:
    async with engine.begin() as conn:
        clusters = await load_clusters(conn, days)

    weights = compute_idf_weights(clusters)
    print(f"Loaded {len(clusters)} clusters from the last {days} days "
          f"and {len(weights)} distinct entity keys (in-set IDF weighting).")

    pairs = score_pairs(clusters, weights, days, min_shared, min_gap_hours)
    pairs = [p for p in pairs if p.score >= threshold]

    if chains:
        print_chains(build_chains(pairs), limit)
    elif csv_path:
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
    parser.add_argument("--min-gap-hours", type=float, default=0.0, help="Minimum time gap between clusters to count as a candidate pair (default: 0, no filter)")
    parser.add_argument("--chains", action="store_true", help="Group candidate pairs into connected-component chains (one predecessor per cluster) instead of printing a flat pair list")
    args = parser.parse_args()

    asyncio.run(main(args.days, args.threshold, args.limit, args.csv, args.min_shared, args.min_gap_hours, args.chains))
