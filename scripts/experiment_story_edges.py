"""
Read-only experiment for "story graph mode" — rewrite against the decision
tree worked out with the user (see the "Feed ranking redesign" design
memory for background). Earlier versions of this script scored ALL
candidate pairs/chains uniformly and patched failure modes one at a time as
they showed up in manual review (generic-entity buckets, topic drift,
bursty splits). This version follows an explicit decision process instead:

  Node 1: collect candidate stories sharing >=1 entity (inverted index).
  Node 2/3/4: rank shared entities by how common they are WITHIN a match,
      and reject any entity whose entity_stats.baseline_rate (slow 75-day
      EMA — a genuinely generic entity like BJP/Supreme Court/India stays
      persistently common) is too high, falling back to the next-most
      supported entity. Entities entity_stats hasn't seen yet fall back to
      in-set document frequency (--max-df-ratio) as an interim stand-in,
      since entity_stats is still young (deployed 2026-08-23) and sparse.
  Node 5: one confirmed-specific entity ("actor") is enough to link stories
      — no min-shared-count hack needed once genericness is checked directly.
  Node 6: every cluster mentioning that actor forms a "topic group".
  Node 6b: subsumption — a topic group that's near-entirely contained in an
      already-kept larger group (e.g. "Sunita Ahuja" vs "Govinda" — nearly
      every Sunita Ahuja story also mentions Govinda, not vice versa) is
      dropped rather than kept as a near-duplicate second group. Run a
      SECOND time after Node 7 (dedup_sub_clusters) — two actors' full
      groups can be mostly disjoint overall (e.g. Dhanush's group has many
      stories with no Kareena Kapoor) while the specific sub-cluster each
      narrows down to is identical, which Node 6b alone can't see since it
      only compares pre-split groups.
  Node 7: within a topic group, sub-cluster IGNORING time, scored on
      entities OTHER than the group's own actor (the actor is shared by
      construction and so is uninformative for telling sub-stories apart —
      e.g. "Govinda's movie news" vs "Govinda's divorce news" need their
      OWN shared entities, not just "Govinda", to stay separate).
  Node 8: within each sub-cluster, flag members whose nearest-neighbor time
      gap is a large multiple of the sub-cluster's typical gap.
  Node 9: those outliers are shown as branches off the main chronological
      trunk, not dropped and not merged in as if they were normal hops.
  Node 10/11: rank chains by importance instead of hand-listing "junk"
      categories (routine content like a daily lottery result never
      naturally ranks high, no dedicated rule needed) — each trunk hop's
      "hotness" is distinct_source_count (how many outlets covered that
      specific development) times the actor's reactivation ratio
      (entity_stats.mention_count_decayed / baseline_rate — is this entity
      spiking above its own norm right now), rolled up across the trunk as
      an EMA (app.services.decay.ema_update, the same function poller.py
      uses for entity_stats) so recent, well-covered, spiking hops
      dominate a chain's rank and old/quiet ones fade — the same
      recency-weighting idea as an EMA in trading.

  Round 5 (build_backdrop_check): actor TYPE, not just frequency/genericity
      — a new is_backdrop(entity_key) predicate, applied alongside is_generic
      in Node 2-4, rejects entities that describe a story's setting rather
      than its subject: every location-typed entity unconditionally, any
      entity enrichment.py's LLM prompt itself flagged "backdrop" (collective
      labels like "Bollywood"), and any organization-typed entity matching a
      real Source name (a publication leaking in as an entity). See
      backend/docs/story-graph-design.md's "Current hypothesis" section for
      the failure cases this targets (Brydon Carse/"derby").

No writes, no new tables. Enrichment now makes one extra ask of its
existing per-cluster LLM call (which entities are backdrop) — no new calls.

Usage (inside the app container, so DATABASE_URL is set):
    python3 scripts/experiment_story_edges.py
    python3 scripts/experiment_story_edges.py --days 60 --limit 30
    python3 scripts/experiment_story_edges.py --generic-percentile 0.9 --subsumption-ratio 0.7
"""
import argparse
import asyncio
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
from app.services.decay import ema_update
from app.services.entity_graph import canonicalize_entity

DEFAULT_HALF_LIFE = timedelta(hours=48)


@dataclass
class Cluster:
    id: int
    headline: str
    first_seen_at: datetime
    last_updated_at: datetime
    distinct_source_count: int
    entity_keys: Set[str] = field(default_factory=set)
    # Entities enrichment flagged as backdrop/context rather than the
    # story's subject (see enrichment.py's ENRICHMENT_SYSTEM_PROMPT) — a
    # subset of entity_keys, not a separate namespace. Empty for
    # rule-based-only enrichment or pre-Round-5 clusters (no signal, not
    # "confirmed not backdrop").
    backdrop_keys: Set[str] = field(default_factory=set)


@dataclass
class TopicGroup:
    actor: str
    actor_display: str
    members: List[Cluster]


@dataclass
class SubCluster:
    topic_group: TopicGroup
    trunk: List[Cluster]     # chronological, non-outlier
    branches: List[Cluster]  # outliers, shown attached but off the trunk
    importance: float = 0.0


# ---------------------------------------------------------------- loading --

async def load_clusters(conn, days: int) -> List[Cluster]:
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
        raw_backdrop_names = set(entities.get("backdrop") or [])
        entity_keys: Set[str] = set()
        backdrop_keys: Set[str] = set()
        for entity_type, field_name in (
            ("person", "persons"),
            ("organization", "organizations"),
            ("location", "locations"),
        ):
            for raw_name in entities.get(field_name, []) or []:
                key = canonicalize_entity(raw_name, entity_type)
                if key:
                    entity_keys.add(key)
                    if raw_name in raw_backdrop_names:
                        backdrop_keys.add(key)
        clusters.append(
            Cluster(
                id=row.id,
                headline=row.headline or "",
                first_seen_at=row.first_seen_at,
                last_updated_at=row.last_updated_at,
                distinct_source_count=row.distinct_source_count or 1,
                entity_keys=entity_keys,
                backdrop_keys=backdrop_keys,
            )
        )
    return clusters


async def load_entity_stats(conn) -> Dict[str, Tuple[float, float, str]]:
    """entity_key -> (baseline_rate, mention_count_decayed, display_name)."""
    result = await conn.execute(
        text("SELECT entity_key, baseline_rate, mention_count_decayed, display_name FROM entity_stats")
    )
    return {row.entity_key: (row.baseline_rate, row.mention_count_decayed, row.display_name) for row in result}


async def load_source_names(conn) -> Set[str]:
    """Canonicalized (as organization-type keys) names of every real news
    source — used to catch publication names that leaked into an
    "organizations" entity extraction (e.g. "livemint", "gadgets_360") as if
    they were a story subject. A structural lookup against ground truth,
    not a hand-maintained denylist — it stays correct as sources.py grows."""
    result = await conn.execute(text("SELECT name FROM sources"))
    keys = set()
    for row in result:
        key = canonicalize_entity(row.name, "organization")
        if key:
            keys.add(key)
    return keys


def compute_idf_weights(clusters: List[Cluster]) -> Dict[str, float]:
    """log(N / df) per entity_key — used only for Node 7's secondary-entity scoring."""
    doc_freq: Dict[str, int] = defaultdict(int)
    for cluster in clusters:
        for key in cluster.entity_keys:
            doc_freq[key] += 1
    n = len(clusters)
    return {key: math.log(n / df) + 1.0 for key, df in doc_freq.items()}


# --------------------------------------------- Node 2/3/4: actor selection --

def build_generic_check(
    clusters: List[Cluster], entity_stats: Dict[str, Tuple[float, float, str]],
    generic_percentile: float, max_df_ratio: float,
):
    """
    Returns a predicate is_generic(entity_key) -> bool.

    Primary signal: entity_stats.baseline_rate (a slow 75-day EMA — real
    long-run commonness), thresholded at the given percentile among
    entities we actually have stats for. entity_stats is young (~700 rows
    as of this script's introduction) so most entities in a given run won't
    have a row yet — those fall back to in-set document frequency
    (--max-df-ratio), the same interim heuristic the previous version of
    this script used as its only signal.
    """
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


def build_backdrop_check(clusters: List[Cluster], source_name_keys: Set[str]):
    """
    Round 5 — actor TYPE, not just frequency/genericity (see
    backend/docs/story-graph-design.md's "Current hypothesis"). Returns a
    predicate is_backdrop(entity_key) -> bool for entities that describe a
    story's setting/context rather than what it's genuinely about, so they
    never get first claim on an actor slot in select_topic_groups —
    independent of Node 3's genericity check, since a backdrop entity can be
    rare/specific (a one-off nightclub name) and still be the wrong kind of
    thing to anchor a chain on.

    Three signals, each catching a different failure mode observed in
    Round 4:
    - Every LOCATION-typed entity, unconditionally — a place is backdrop by
      definition (the "Brydon Carse nightclub incident" bug: "derby" is a
      location, not a subject).
    - Any entity enrichment itself flagged "backdrop" for at least one
      cluster (organizations/collective labels like "Bollywood") — a global
      union across the whole window, on the assumption that whether an
      entity is subject-shaped is a property of the entity, not the
      specific story it appears in.
    - Any organization-typed entity matching a real Source name — catches a
      publication name leaking in as if it were a story subject.
    """
    backdrop_keys: Set[str] = set()
    for cluster in clusters:
        backdrop_keys.update(cluster.backdrop_keys)

    def is_backdrop(entity_key: str) -> bool:
        if entity_key.startswith("location:"):
            return True
        if entity_key in backdrop_keys:
            return True
        if entity_key in source_name_keys:
            return True
        return False

    return is_backdrop


def select_topic_groups(
    clusters: List[Cluster], is_generic, is_backdrop, subsumption_ratio: float,
) -> List[TopicGroup]:
    """
    Node 1 (inverted index) + Node 2/3/4 (reject generic actors) + Node 5
    (one specific entity is enough) + Node 6 (gather all members) + Node 6b
    (subsumption). "Within-match frequency" (Node 2) is realized simply as
    processing candidate actors in descending order of support (# clusters
    mentioning them) — the dominant/lead entity in an overlapping pair
    (e.g. Govinda over Sunita Ahuja) naturally has higher support and gets
    first claim on the shared members via subsumption, without needing to
    re-derive "which entity wins" per individual pair.
    """
    by_entity: Dict[str, List[Cluster]] = defaultdict(list)
    for cluster in clusters:
        for key in cluster.entity_keys:
            by_entity[key].append(cluster)

    candidates = [
        (key, members) for key, members in by_entity.items()
        if len(members) >= 2 and not is_generic(key) and not is_backdrop(key)
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


# --------------------------------------- Node 7/8/9: sub-cluster + outliers --

def sub_cluster_topic_group(
    group: TopicGroup, weights: Dict[str, float], min_shared: int,
) -> List[List[Cluster]]:
    """
    Node 7: union-find over pairwise overlap of entities OTHER than the
    group's own actor. The actor is shared by every member by construction,
    so it carries zero information for telling sub-stories apart — only
    the OTHER entities two members happen to also share (a movie title, a
    spouse's name, a co-star) can split "Govinda's movie news" from
    "Govinda's divorce news" within the same topic group.
    """
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


def split_outliers(sub_cluster: List[Cluster], outlier_gap_multiplier: float) -> Tuple[List[Cluster], List[Cluster]]:
    """
    Node 8/9: nearest-neighbor time-gap outlier detection. A member whose
    gap to its closest neighbor (in either direction) is a large multiple
    of the sub-cluster's typical nearest-neighbor gap gets pulled out as a
    branch rather than counted as part of the main chronological trunk.
    Needs >=3 members to have a meaningful "typical gap" to compare against.
    """
    if len(sub_cluster) < 3:
        return sub_cluster, []

    times = [c.first_seen_at for c in sub_cluster]
    nn_gaps = []
    for i in range(len(sub_cluster)):
        candidates = []
        if i > 0:
            candidates.append(times[i] - times[i - 1])
        if i < len(sub_cluster) - 1:
            candidates.append(times[i + 1] - times[i])
        nn_gaps.append(min(candidates))

    sorted_gaps = sorted(g.total_seconds() for g in nn_gaps)
    median_gap = sorted_gaps[len(sorted_gaps) // 2]
    if median_gap <= 0:
        median_gap = 60.0  # 1 minute floor so a burst of near-simultaneous items doesn't flag everything

    trunk, branches = [], []
    for cluster, gap in zip(sub_cluster, nn_gaps):
        if gap.total_seconds() > median_gap * outlier_gap_multiplier:
            branches.append(cluster)
        else:
            trunk.append(cluster)
    return trunk, branches


# ------------------------------------------------ Node 10/11: importance --

def score_importance(
    trunk: List[Cluster], reactivation_ratio: float, half_life: timedelta,
) -> float:
    """
    Node 10: per-hop hotness = distinct_source_count (coverage breadth) x
    reactivation_ratio (is the actor spiking above its own baseline right
    now — entity_stats.mention_count_decayed / baseline_rate, constant per
    topic group). Node 11: EMA over trunk hops in time order, same
    normalized-EMA formula poller.py already uses for entity_stats, so
    recent/well-covered/spiking hops dominate and old ones fade — routine
    content (a daily lottery result, a recurring gadget-spec drop) never
    spikes, so its EMA importance stays low without a dedicated "is this
    junk" rule.
    """
    if not trunk:
        return 0.0
    ema = trunk[0].distinct_source_count * reactivation_ratio
    previous_time = trunk[0].first_seen_at
    for cluster in trunk[1:]:
        hotness = cluster.distinct_source_count * reactivation_ratio
        elapsed = cluster.first_seen_at - previous_time
        ema = ema_update(ema, elapsed, half_life, hotness)
        previous_time = cluster.first_seen_at
    return ema


# --------------------------------------------------- sub-cluster dedup --

def dedup_sub_clusters(sub_clusters: List[SubCluster], subsumption_ratio: float) -> List[SubCluster]:
    """
    Second subsumption pass, run AFTER Node 7's split, not just at Node 6b.
    Node 6b dedupes at the topic-group level (before Node 7), but two
    different actors' FULL groups can be mostly disjoint overall (Dhanush's
    group includes many stories with no Kareena Kapoor in them) while the
    specific SUB-CLUSTER produced once both narrow down to just their
    shared story is identical or near-identical (e.g. dhanush, kareena_kapoor,
    and bollywood all independently anchoring the exact same 2-cluster
    Bhansali-film-casting story). Node 6b can't see that redundancy since it
    only compares full groups; this pass compares the final sub-clusters
    (trunk + branches) instead, keeping the largest of each near-duplicate
    set.
    """
    scored = [(len(sc.trunk) + len(sc.branches), sc) for sc in sub_clusters]
    scored.sort(key=lambda t: t[0], reverse=True)

    kept: List[SubCluster] = []
    kept_id_sets: List[Set[int]] = []
    for _, sc in scored:
        member_ids = {c.id for c in sc.trunk + sc.branches}
        if not member_ids:
            continue
        subsumed = any(
            len(member_ids & kept_ids) / len(member_ids) >= subsumption_ratio
            for kept_ids in kept_id_sets
        )
        if subsumed:
            continue
        kept.append(sc)
        kept_id_sets.append(member_ids)

    return kept


# --------------------------------------------------------------- printing --

def print_chains(
    sub_clusters: List[SubCluster], limit: int, entity_stats: Dict[str, Tuple[float, float, str]],
) -> None:
    sub_clusters = [sc for sc in sub_clusters if len(sc.trunk) >= 2]
    sub_clusters.sort(key=lambda sc: sc.importance, reverse=True)
    print(f"\n{len(sub_clusters)} story chains (trunk length >= 2), ranked by EMA importance, "
          f"showing up to {limit}:\n")
    for sc in sub_clusters[:limit]:
        ratio = None
        if sc.topic_group.actor in entity_stats:
            decayed, baseline, _ = entity_stats[sc.topic_group.actor]
            ratio = decayed / max(baseline, 0.05)
        ratio_str = f", reactivation={ratio:.2f}" if ratio is not None else ""
        print(f"=== actor: {sc.topic_group.actor_display}  importance={sc.importance:.2f}{ratio_str} "
              f"(trunk={len(sc.trunk)}, branches={len(sc.branches)}) ===")
        for cluster in sc.trunk:
            when = cluster.first_seen_at.strftime("%Y-%m-%d %H:%M")
            print(f"  [{when}] #{cluster.id:6d} (sources={cluster.distinct_source_count}) {cluster.headline[:85]}")
        for cluster in sc.branches:
            when = cluster.first_seen_at.strftime("%Y-%m-%d %H:%M")
            print(f"      ↳ branch [{when}] #{cluster.id:6d} {cluster.headline[:80]}")
        print()


# --------------------------------------------------------------------- main --

async def main(
    days: int, limit: int, generic_percentile: float, max_df_ratio: float,
    subsumption_ratio: float, min_shared: int, outlier_gap_multiplier: float,
    half_life_hours: float,
) -> None:
    async with engine.begin() as conn:
        clusters = await load_clusters(conn, days)
        entity_stats = await load_entity_stats(conn)
        source_name_keys = await load_source_names(conn)

    print(f"Loaded {len(clusters)} clusters from the last {days} days "
          f"and {len(entity_stats)} entity_stats rows.")

    is_generic = build_generic_check(clusters, entity_stats, generic_percentile, max_df_ratio)
    is_backdrop = build_backdrop_check(clusters, source_name_keys)
    topic_groups = select_topic_groups(clusters, is_generic, is_backdrop, subsumption_ratio)
    print(f"{len(topic_groups)} topic groups survived generic-entity/backdrop filtering + subsumption.")

    weights = compute_idf_weights(clusters)
    half_life = timedelta(hours=half_life_hours)

    sub_clusters: List[SubCluster] = []
    for group in topic_groups:
        for raw_sub in sub_cluster_topic_group(group, weights, min_shared):
            trunk, branches = split_outliers(raw_sub, outlier_gap_multiplier)
            ratio = 1.0
            if group.actor in entity_stats:
                decayed, baseline, _ = entity_stats[group.actor]
                ratio = decayed / max(baseline, 0.05)
            importance = score_importance(trunk, ratio, half_life)
            sub_clusters.append(SubCluster(topic_group=group, trunk=trunk, branches=branches, importance=importance))

    before = len(sub_clusters)
    sub_clusters = dedup_sub_clusters(sub_clusters, subsumption_ratio)
    print(f"{len(sub_clusters)} sub-clusters survived cross-actor dedup (of {before} raw).")

    print_chains(sub_clusters, limit, entity_stats)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=60, help="Lookback window in days (default: 60)")
    parser.add_argument("--limit", type=int, default=30, help="Max chains to print, ranked by importance (default: 30)")
    parser.add_argument("--generic-percentile", type=float, default=0.95, help="Entities at/above this percentile of entity_stats.baseline_rate are treated as generic (default: 0.95)")
    parser.add_argument("--max-df-ratio", type=float, default=0.015, help="Fallback genericness cutoff (in-set doc frequency) for entities with no entity_stats row yet (default: 0.015)")
    parser.add_argument("--subsumption-ratio", type=float, default=0.8, help="A topic group >= this fraction contained in a larger already-kept group is dropped (default: 0.8)")
    parser.add_argument("--min-shared", type=int, default=1, help="Node 7: minimum secondary (non-actor) shared entities to sub-cluster two members together (default: 1)")
    parser.add_argument("--outlier-gap-multiplier", type=float, default=4.0, help="Node 8: a member is an outlier branch if its nearest-neighbor gap exceeds this multiple of the sub-cluster's median gap (default: 4.0)")
    parser.add_argument("--half-life-hours", type=float, default=48.0, help="Node 11: EMA half-life for chain importance (default: 48)")
    args = parser.parse_args()

    asyncio.run(main(
        args.days, args.limit, args.generic_percentile, args.max_df_ratio,
        args.subsumption_ratio, args.min_shared, args.outlier_gap_multiplier,
        args.half_life_hours,
    ))
