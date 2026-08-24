"""
Read-only experiment for "story graph mode" (see the "Feed ranking redesign"
design doc/memory): tests whether a cheap entity-overlap heuristic alone is
strong enough to detect story-to-story continuation ("this cluster is a
follow-up/development of that earlier cluster"), before deciding whether an
LLM confirmation pass is needed on top of it.

No writes, no new tables, no LLM calls. Scores candidate (earlier, later)
cluster pairs within a time window using canonicalized shared entities
(app.services.entity_graph.canonicalize_entity), weighted by rarity via
entity_stats.baseline_rate, and prints them for manual review.

Usage (inside the app container, so DATABASE_URL is set):
    python3 scripts/experiment_story_edges.py
    python3 scripts/experiment_story_edges.py --days 60 --threshold 0.15 --limit 100
    python3 scripts/experiment_story_edges.py --csv /tmp/story_edges.csv
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

# Fallback weight for entity keys with no entity_stats row yet (treat as
# moderately common rather than crashing or over-weighting brand-new entities).
DEFAULT_BASELINE_RATE = 0.5
EPSILON = 0.05


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


async def load_baseline_rates(conn) -> Dict[str, float]:
    result = await conn.execute(text("SELECT entity_key, baseline_rate FROM entity_stats"))
    return {row.entity_key: row.baseline_rate for row in result}


def score_pairs(
    clusters: List[Cluster], baseline_rates: Dict[str, float], days: int
) -> List[CandidatePair]:
    # Inverted index: entity_key -> cluster ids that mention it, so we only
    # ever compare clusters that share at least one entity.
    index: Dict[str, List[Cluster]] = defaultdict(list)
    for cluster in clusters:
        for key in cluster.entity_keys:
            index[key].append(cluster)

    max_gap = timedelta(days=days)
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
                if later.first_seen_at - earlier.first_seen_at > max_gap:
                    continue
                seen_pairs.add(pair_key)

                shared_keys = earlier.entity_keys & later.entity_keys
                shared_weighted = []
                total_weight = 0.0
                for shared_key in shared_keys:
                    rate = baseline_rates.get(shared_key, DEFAULT_BASELINE_RATE)
                    weight = 1.0 / (rate + EPSILON)
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


async def main(days: int, threshold: float, limit: int, csv_path: Optional[str]) -> None:
    async with engine.begin() as conn:
        clusters = await load_clusters(conn, days)
        baseline_rates = await load_baseline_rates(conn)

    print(f"Loaded {len(clusters)} clusters from the last {days} days "
          f"and {len(baseline_rates)} entity_stats rows.")

    pairs = score_pairs(clusters, baseline_rates, days)
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
    args = parser.parse_args()

    asyncio.run(main(args.days, args.threshold, args.limit, args.csv))
