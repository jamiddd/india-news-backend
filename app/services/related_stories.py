"""
"Related stories" — a flat, non-timeline byproduct of the story-graph
experiment (see backend/docs/story-graph-design.md and
scripts/experiment_story_edges.py). That script chains clusters into
chronological trunks/branches, which is still buggy (multi-actor
fragmentation, Node 7/8 can drop a real member). This module ports only the
grouping logic validated as working by manual review — Nodes 1-7 plus both
subsumption/dedup passes — to answer a narrower question: "what else is this
story connected to?", with no chronology or trunk/branch concept at all.

Known limitation carried over from the source script: because grouping can
still fragment a story across more than one actor label, a query here may
occasionally return an incomplete set of related stories. Under-showing is
the accepted failure mode for a discovery feature — see the design doc's
"Next steps" for the actor-type refinement that would reduce this.
"""
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy import text

from app.services.entity_graph import canonicalize_entity
from app.redis_client import get_redis_client

DAYS = 60
GENERIC_PERCENTILE = 0.95
MAX_DF_RATIO = 0.015
SUBSUMPTION_RATIO = 0.8
MIN_SHARED = 1

# Both load_clusters/load_entity_stats below feed a per-cluster-id cache
# already (see main.py's GET /clusters/{id}/related), but that cache is
# keyed on the specific cluster_id being asked about — so any two different
# "what's related to X" requests within the same few minutes still each
# re-ran these two queries from scratch: the *entire* 60-day cluster window
# with its entities JSON, and a full entity_stats table scan. This caches
# those two inputs themselves, independent of which cluster_id is asked
# about, so only the (cheap, in-Python) graph rebuild repeats per request.
RELATED_INPUTS_CACHE_TTL_SECONDS = 300


async def _cache_get(key: str):
    try:
        return await get_redis_client().get(key)
    except Exception:
        return None


async def _cache_set(key: str, value: str, ttl: int = RELATED_INPUTS_CACHE_TTL_SECONDS):
    try:
        await get_redis_client().setex(key, ttl, value)
    except Exception:
        pass


@dataclass
class Cluster:
    id: int
    headline: str
    first_seen_at: datetime
    last_updated_at: datetime
    distinct_source_count: int
    entity_keys: Set[str] = field(default_factory=set)


@dataclass
class TopicGroup:
    actor: str
    actor_display: str
    members: List[Cluster]


async def load_clusters(conn, days: int) -> List[Cluster]:
    cache_key = f"cache:related:clusters:{days}"
    cached = await _cache_get(cache_key)
    if cached is not None:
        return [
            Cluster(
                id=d["id"],
                headline=d["headline"],
                first_seen_at=datetime.fromisoformat(d["first_seen_at"]),
                last_updated_at=datetime.fromisoformat(d["last_updated_at"]),
                distinct_source_count=d["distinct_source_count"],
                entity_keys=set(d["entity_keys"]),
            )
            for d in json.loads(cached)
        ]

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    result = await conn.execute(
        text(
            """
            SELECT id, headline, entities, first_seen_at, last_updated_at,
                   distinct_source_count
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
                distinct_source_count=row.distinct_source_count or 1,
                entity_keys=entity_keys,
            )
        )

    await _cache_set(cache_key, json.dumps([
        {
            "id": c.id,
            "headline": c.headline,
            "first_seen_at": c.first_seen_at.isoformat(),
            "last_updated_at": c.last_updated_at.isoformat(),
            "distinct_source_count": c.distinct_source_count,
            "entity_keys": sorted(c.entity_keys),
        }
        for c in clusters
    ]))
    return clusters


async def load_entity_stats(conn) -> Dict[str, Tuple[float, float, str]]:
    """entity_key -> (baseline_rate, mention_count_decayed, display_name)."""
    cache_key = "cache:related:entity_stats"
    cached = await _cache_get(cache_key)
    if cached is not None:
        return {k: tuple(v) for k, v in json.loads(cached).items()}

    result = await conn.execute(
        text("SELECT entity_key, baseline_rate, mention_count_decayed, display_name FROM entity_stats")
    )
    stats = {row.entity_key: (row.baseline_rate, row.mention_count_decayed, row.display_name) for row in result}
    await _cache_set(cache_key, json.dumps(stats))
    return stats


def build_generic_check(
    clusters: List[Cluster], entity_stats: Dict[str, Tuple[float, float, str]],
    generic_percentile: float, max_df_ratio: float,
):
    """Same predicate as experiment_story_edges.py's Node 2/3/4 — kept in
    lockstep with that script; see it for the full rationale."""
    from collections import defaultdict

    baseline_rates = sorted(v[0] for v in entity_stats.values() if v[0] > 0)
    cutoff = None
    if baseline_rates:
        idx = min(int(len(baseline_rates) * generic_percentile), len(baseline_rates) - 1)
        cutoff = baseline_rates[idx]

    doc_freq: Dict[str, int] = defaultdict(int)
    for cluster in clusters:
        for key in cluster.entity_keys:
            doc_freq[key] += 1
    n = len(clusters)

    def is_generic(entity_key: str) -> bool:
        if entity_key in entity_stats and cutoff is not None:
            return entity_stats[entity_key][0] >= cutoff
        return (doc_freq.get(entity_key, 0) / n) > max_df_ratio if n else False

    return is_generic


def select_topic_groups(
    clusters: List[Cluster], is_generic, subsumption_ratio: float,
) -> List[TopicGroup]:
    """Node 1 (inverted index) + Node 2/3/4 (reject generic actors) + Node 5
    (one specific entity is enough) + Node 6 (gather members) + Node 6b
    (subsumption). See experiment_story_edges.py for full rationale."""
    from collections import defaultdict

    by_entity: Dict[str, List[Cluster]] = defaultdict(list)
    for cluster in clusters:
        for key in cluster.entity_keys:
            by_entity[key].append(cluster)

    candidates = [
        (key, members) for key, members in by_entity.items()
        if len(members) >= 2 and not is_generic(key)
    ]
    candidates.sort(key=lambda kv: len(kv[1]), reverse=True)

    kept: List[TopicGroup] = []
    kept_id_sets: List[Set[int]] = []
    for key, members in candidates:
        member_ids = {c.id for c in members}
        subsumed = any(
            len(member_ids & kept_ids) / len(member_ids) >= subsumption_ratio
            for kept_ids in kept_id_sets
        )
        if subsumed:
            continue
        display = key.split(":", 1)[1] if ":" in key else key
        kept.append(TopicGroup(actor=key, actor_display=display, members=members))
        kept_id_sets.append(member_ids)

    return kept


def sub_cluster_topic_group(group: TopicGroup, min_shared: int) -> List[List[Cluster]]:
    """Node 7 — union-find over shared non-actor entities. See
    experiment_story_edges.py for full rationale."""
    from collections import defaultdict

    members = group.members
    parent: Dict[int, int] = {c.id: c.id for c in members}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    index: Dict[str, List[Cluster]] = defaultdict(list)
    for cluster in members:
        for key in cluster.entity_keys:
            if key == group.actor:
                continue
            index[key].append(cluster)

    for key, bucket in index.items():
        if len(bucket) < 2:
            continue
        for i in range(len(bucket)):
            for j in range(i + 1, len(bucket)):
                a, b = bucket[i], bucket[j]
                shared = (a.entity_keys - {group.actor}) & (b.entity_keys - {group.actor})
                if len(shared) >= min_shared:
                    union(a.id, b.id)

    groups: Dict[int, List[Cluster]] = defaultdict(list)
    for cluster in members:
        groups[find(cluster.id)].append(cluster)

    return [sorted(g, key=lambda c: c.first_seen_at) for g in groups.values()]


def dedup_sub_clusters(
    sub_clusters: List[Tuple[TopicGroup, List[Cluster]]], subsumption_ratio: float,
) -> List[Tuple[TopicGroup, List[Cluster]]]:
    """Second subsumption pass across all topic groups' sub-clusters, keeping
    the largest of any near-duplicate set. See experiment_story_edges.py's
    dedup_sub_clusters for full rationale."""
    scored = [(len(members), (group, members)) for group, members in sub_clusters]
    scored.sort(key=lambda t: t[0], reverse=True)

    kept: List[Tuple[TopicGroup, List[Cluster]]] = []
    kept_id_sets: List[Set[int]] = []
    for _, (group, members) in scored:
        member_ids = {c.id for c in members}
        if not member_ids:
            continue
        subsumed = any(
            len(member_ids & kept_ids) / len(member_ids) >= subsumption_ratio
            for kept_ids in kept_id_sets
        )
        if subsumed:
            continue
        kept.append((group, members))
        kept_id_sets.append(member_ids)

    return kept


def _relevance_score(cluster: Cluster, group: TopicGroup, entity_stats: Dict[str, Tuple[float, float, str]]) -> float:
    """distinct_source_count (coverage breadth) x reactivation_ratio (is the
    group's anchor entity spiking above its own norm right now) — the same
    per-hop hotness formula as Node 10 in experiment_story_edges.py, without
    the EMA rollup since there's no chain to roll up over."""
    ratio = 1.0
    if group.actor in entity_stats:
        decayed, baseline, _ = entity_stats[group.actor]
        ratio = decayed / max(baseline, 0.05)
    return cluster.distinct_source_count * ratio


async def find_related_clusters(
    conn, cluster_id: int, days: int = DAYS, sort: str = "relevance", limit: int = 20,
) -> Tuple[List[Cluster], Optional[str]]:
    """Runs Nodes 1-7 + both dedup passes over the lookback window, finds the
    sub-cluster(s) containing `cluster_id`, unions their other members, and
    returns them sorted plus the winning actor's display name (None if
    `cluster_id` wasn't found in the window at all, distinct from "found but
    no related stories")."""
    clusters = await load_clusters(conn, days)
    by_id = {c.id: c for c in clusters}
    if cluster_id not in by_id:
        return [], None

    entity_stats = await load_entity_stats(conn)
    is_generic = build_generic_check(clusters, entity_stats, GENERIC_PERCENTILE, MAX_DF_RATIO)
    topic_groups = select_topic_groups(clusters, is_generic, SUBSUMPTION_RATIO)

    raw_sub_clusters: List[Tuple[TopicGroup, List[Cluster]]] = []
    for group in topic_groups:
        for members in sub_cluster_topic_group(group, MIN_SHARED):
            raw_sub_clusters.append((group, members))

    sub_clusters = dedup_sub_clusters(raw_sub_clusters, SUBSUMPTION_RATIO)

    related: Dict[int, Cluster] = {}
    winning_group: Optional[TopicGroup] = None
    for group, members in sub_clusters:
        member_ids = {c.id for c in members}
        if cluster_id not in member_ids:
            continue
        winning_group = group
        for cluster in members:
            if cluster.id != cluster_id:
                related[cluster.id] = cluster
        break  # dedup already guarantees no two kept sub-clusters share this many members

    result = list(related.values())
    if sort == "time":
        result.sort(key=lambda c: c.first_seen_at, reverse=True)
    elif winning_group is not None:
        result.sort(key=lambda c: _relevance_score(c, winning_group, entity_stats), reverse=True)

    winning_actor = winning_group.actor_display if winning_group is not None else None
    return result[:limit], winning_actor
