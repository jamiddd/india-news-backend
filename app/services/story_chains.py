"""Story-timeline chaining — "story so far", second attempt.

See backend/docs/story-graph-design.md for the full history of the first
attempt (built when clustering itself was 98.5% singletons, abandoned
unvalidated) and scripts/eval_story_chains.py for the harness that
grid-tunes CHAIN_PARAMS below against a labelled pair set — mirror any
change into that script's own params in the same commit, same convention
as clustering-rework-handoff.md's SHIPPED_PARAMS, or the harness measures
an algorithm nobody is running.

NOT WIRED INTO ANY ENDPOINT YET. This module is Phase 3 of the plan
(candidate generation + actor selection + chain assembly); the API/UI
layer is a later step, deliberately not built until CHAIN_PARAMS below is
confirmed against real labelled data rather than the provisional grid run
this shipped with (see the comment on CHAIN_PARAMS).

What's reused from the first attempt (validated by manual review there,
and in related_stories.py's port of the same logic):
  - candidate generation by canonicalized shared entity (Node 1)
  - the Node 3 genericity check: entity_stats.baseline_rate, falling back
    to in-set document frequency for entities the table hasn't matured on
  - Round 5's actor-type filter: locations are backdrop unconditionally,
    entities.backdrop (LLM-flagged), organizations matching a real Source
    name — structural checks, never a hand-typed denylist

What's deliberately different from related_stories.py's own port of this
logic (Nodes 1-7 + dedup):
  - No Node 7/8 (time-agnostic actor-exclusion sub-clustering + outlier
    branching). That machinery was never fixed in the first attempt — it
    silently dropped real chain members — and there's no evidence yet
    that clean, high-precision clusters still need it. Each surviving
    topic group IS the chain; see eval_story_chains.py's build_chains
    docstring for the full reasoning.
  - Node 6b (subsumption) MERGES an overlapping smaller group into the
    larger one instead of dropping it, so a member unique to the smaller
    group is never silently lost — that loss was exactly the class of bug
    Node 7/8 was never cleared of.
  - This module additionally applies the backdrop filter, which
    related_stories.py's port does not.

Egress: same lean-column query and Redis-cached inputs as
related_stories.py, sharing that reasoning (Supabase meters egress) rather
than duplicating a second DB round-trip for the same data shape. See
scripts/check_timeline_readiness.py for the measured fetch size (~21MB/30d
as of 2026-09-07, no article bodies).
"""
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

from sqlalchemy import text

from app.services.entity_graph import canonicalize_entity
from app.services.feed_gate import gate_cache_marker, gate_min_sources
from app.redis_client import get_redis_client

DAYS = 30

# PROVISIONAL — chosen from a grid run against the OLD 2-class label set
# (continues/unrelated), which conflated genuine chaining errors with
# same-day duplicate-cluster pairs clustering itself failed to merge (see
# the 2026-09-07 session: ~half the measured false positives were that,
# not real mistakes). Re-run scripts/eval_story_chains.py's `grid` against
# the 3-class relabel (same_event/continues/unrelated) before trusting
# these numbers or wiring this module into an endpoint. Mirror any change
# here into that script's own defaults in the same commit.
GENERIC_PERCENTILE = 0.99
GENERIC_METRIC = "in_set_df"  # "in_set_df" beat "baseline_rate" on real data:
# entity_stats (9.3k rows as of 2026-09-07) isn't dense enough yet to catch
# this window's own generic entities — the baseline_rate config produced a
# 134-cluster blob chain (topic-blob failure) where in_set_df, computed
# fresh from the window itself, stayed under the plausibility cap.
SUBSUMPTION_RATIO = 0.9

# A chain past this size in a 30-day window is a topic blob, not one
# story — same reasoning as related_stories.py's MAX_DF_RATIO-adjacent
# guards and eval_clustering.py's MAX_PLAUSIBLE_CLUSTER. A genuine
# multi-week saga (a court case, an election) can legitimately run long;
# this exists to catch a generic entity slipping through the filter and
# swallowing unrelated stories, not to cap real sagas tightly.
MAX_PLAUSIBLE_CHAIN = 40

# Mirrors related_stories.py's RELATED_INPUTS_CACHE_TTL_SECONDS: cache the
# DB inputs (clusters, entity_stats, source names), not the final chain
# assignment — the assignment rebuild is cheap, in-Python graph work once
# the inputs are loaded, so there is no need for a second cache layer on
# top, and one avoids ever serving a stale chain across a cache flush that
# a fresh cluster/entity write should immediately be reflected in.
CACHE_TTL_SECONDS = 300


async def _cache_get(key: str):
    try:
        return await get_redis_client().get(key)
    except Exception:
        return None


async def _cache_set(key: str, value: str, ttl: int = CACHE_TTL_SECONDS):
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
    # canonical entity key -> is it structurally backdrop (Round 5 signal:
    # location type, LLM-flagged entities.backdrop, or an organization
    # matching a real Source name), independent of genericity (Node 3 is a
    # separate axis — an entity can be non-generic and still backdrop).
    entity_backdrop: Dict[str, bool] = field(default_factory=dict)


async def load_clusters(conn, days: int) -> List[Cluster]:
    gate = gate_cache_marker()
    cache_key = f"cache:timeline:clusters:{gate}:{days}"
    cached = await _cache_get(cache_key)
    if cached is not None:
        return [
            Cluster(
                id=d["id"],
                headline=d["headline"],
                first_seen_at=datetime.fromisoformat(d["first_seen_at"]),
                last_updated_at=datetime.fromisoformat(d["last_updated_at"]),
                distinct_source_count=d["distinct_source_count"],
                entity_backdrop=d["entity_backdrop"],
            )
            for d in json.loads(cached)
        ]

    source_names = await load_source_names(conn)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    result = await conn.execute(
        text(
            """
            SELECT id, headline, entities, first_seen_at, last_updated_at,
                   distinct_source_count
            FROM story_clusters
            WHERE first_seen_at >= :cutoff
              AND distinct_source_count >= :min_sources
            ORDER BY first_seen_at ASC
            """
        ),
        # Same reasoning as related_stories.py: a corroborated story must
        # not chain to single-source ones the feed itself hides when the
        # gate is on.
        {"cutoff": cutoff, "min_sources": gate_min_sources()},
    )
    clusters: List[Cluster] = []
    for row in result:
        entities = row.entities or {}
        llm_backdrop_raw = {
            (n or "").strip().lower() for n in (entities.get("backdrop") or [])
        }
        entity_backdrop: Dict[str, bool] = {}
        for entity_type, field_name in (
            ("person", "persons"),
            ("organization", "organizations"),
            ("location", "locations"),
        ):
            for raw_name in entities.get(field_name) or []:
                key = canonicalize_entity(raw_name, entity_type)
                if not key:
                    continue
                is_backdrop = (
                    entity_type == "location"
                    or (raw_name or "").strip().lower() in llm_backdrop_raw
                    or (entity_type == "organization"
                        and (raw_name or "").strip().lower() in source_names)
                )
                entity_backdrop[key] = entity_backdrop.get(key, False) or is_backdrop

        clusters.append(Cluster(
            id=row.id,
            headline=row.headline or "",
            first_seen_at=row.first_seen_at,
            last_updated_at=row.last_updated_at,
            distinct_source_count=row.distinct_source_count or 1,
            entity_backdrop=entity_backdrop,
        ))

    await _cache_set(cache_key, json.dumps([
        {
            "id": c.id,
            "headline": c.headline,
            "first_seen_at": c.first_seen_at.isoformat(),
            "last_updated_at": c.last_updated_at.isoformat(),
            "distinct_source_count": c.distinct_source_count,
            "entity_backdrop": c.entity_backdrop,
        }
        for c in clusters
    ]))
    return clusters


async def load_baseline_rates(conn) -> Dict[str, float]:
    cache_key = "cache:timeline:baseline_rates"
    cached = await _cache_get(cache_key)
    if cached is not None:
        return json.loads(cached)

    result = await conn.execute(text("SELECT entity_key, baseline_rate FROM entity_stats"))
    rates = {row.entity_key: row.baseline_rate for row in result}
    await _cache_set(cache_key, json.dumps(rates))
    return rates


async def load_source_names(conn) -> Set[str]:
    """Real outlet names, lower-cased, for the Round 5 organization-matches-
    a-Source check. Cached separately from clusters/entity_stats since it
    changes far less often (only via seed_sources.py)."""
    cache_key = "cache:timeline:source_names"
    cached = await _cache_get(cache_key)
    if cached is not None:
        return set(json.loads(cached))

    result = await conn.execute(text("SELECT name FROM sources"))
    names = {row.name.strip().lower() for row in result if row.name}
    await _cache_set(cache_key, json.dumps(sorted(names)), ttl=3600)
    return names


def _generic_cutoff(values: List[float], percentile: float) -> float:
    """Rank-based, not value-based: picks whatever frequency sits at the
    Nth percentile position, not "frequencies above X". This is fine when
    the entity population is large and smoothly distributed (production:
    38,942 distinct entities/30d as of 2026-09-07, only 71% singleton) —
    verified by the real grid run in scripts/eval_story_chains.py, which
    produced bounded, non-degenerate chains. It is fragile on a small or
    sparse population: if there's a gap in the frequency distribution near
    that rank (few entities between "normal" and "clearly generic"), the
    handful that land in the gap get arbitrarily flagged regardless of
    whether their actual frequency is meaningfully high — hit repeatedly
    while smoke-testing this module against small synthetic fixtures. Not
    a new flaw: related_stories.py's build_generic_check uses the same
    technique and inherits the same fragility, just never surfaced there
    because real entity populations stay well clear of the failure mode.
    Not fixed here — no evidence yet that it manifests at production
    scale, and there is no theory-only fix worth preferring over what's
    actually been measured to work.
    """
    if not values:
        return 1.0
    s = sorted(values)
    idx = min(int(len(s) * percentile), len(s) - 1)
    return s[idx]


def build_chains(
    clusters: List[Cluster],
    baseline_rates: Dict[str, float],
    generic_percentile: float = GENERIC_PERCENTILE,
    generic_metric: str = GENERIC_METRIC,
    subsumption_ratio: float = SUBSUMPTION_RATIO,
) -> Dict[int, FrozenSet[int]]:
    """Returns cluster id -> the frozenset of cluster ids in its final
    chain (including itself). A cluster with no chain maps to {its own id}.

    Mirrors scripts/eval_story_chains.py's build_chains exactly (id-keyed
    here instead of index-keyed, since callers want cluster ids) — keep the
    two in lockstep; the eval script exists specifically to validate this
    function's logic before it ships.
    """
    n = len(clusters)
    by_id = {c.id: c for c in clusters}

    doc_freq: Dict[str, int] = {}
    for c in clusters:
        for key in c.entity_backdrop:
            doc_freq[key] = doc_freq.get(key, 0) + 1
    in_set_df = {k: v / n for k, v in doc_freq.items()} if n else {}

    if generic_metric == "baseline_rate" and baseline_rates:
        metric_values = list(baseline_rates.values())
    else:
        metric_values = list(in_set_df.values())
    cutoff = _generic_cutoff(metric_values, generic_percentile)

    def is_generic(key: str) -> bool:
        if generic_metric == "baseline_rate" and key in baseline_rates:
            return baseline_rates[key] >= cutoff
        return in_set_df.get(key, 0.0) >= cutoff

    # Node 6, simplified — see this module's docstring and
    # eval_story_chains.py's build_chains for why Node 2-4's per-match
    # actor arbitration isn't needed once backdrop/generic entities are
    # excluded at the entity level: any surviving specific entity is a
    # valid group-forming key on its own.
    groups: Dict[str, Set[int]] = {}
    for c in clusters:
        for key, is_backdrop in c.entity_backdrop.items():
            if is_backdrop:
                continue
            if is_generic(key):
                continue
            groups.setdefault(key, set()).add(c.id)

    # Node 6b, adapted to merge instead of drop — see module docstring.
    ordered = sorted(groups.values(), key=len, reverse=True)
    kept: List[Set[int]] = []
    for group in ordered:
        if len(group) < 2:
            continue
        merged = False
        for k in kept:
            overlap = len(group & k)
            if overlap / len(group) >= subsumption_ratio:
                k.update(group)
                merged = True
                break
        if not merged:
            kept.append(set(group))

    assignment: Dict[int, FrozenSet[int]] = {c.id: frozenset({c.id}) for c in clusters}
    for group in kept:
        frozen = frozenset(group)
        for cid in group:
            if cid in by_id:
                assignment[cid] = frozen
    return assignment


async def get_story_timeline(
    conn, cluster_id: int, days: int = DAYS,
) -> Tuple[List[Cluster], Optional[str]]:
    """The clusters chained with `cluster_id`, sorted chronologically (the
    "story so far" order), plus a display label for the chain's anchor
    entity — or ([], None) if `cluster_id` isn't in the lookback window at
    all, distinct from "found but not part of any chain" ([cluster_id
    itself excluded -> empty list], "").

    A chain past MAX_PLAUSIBLE_CHAIN is treated as a topic-blob failure and
    returned as if ungrouped, rather than shown to a user as one story —
    same reasoning as the eval harness's blob rejection in `grid`.
    """
    clusters = await load_clusters(conn, days)
    by_id = {c.id: c for c in clusters}
    if cluster_id not in by_id:
        return [], None

    baseline_rates = await load_baseline_rates(conn)
    assignment = build_chains(clusters, baseline_rates)

    chain_ids = assignment[cluster_id]
    if len(chain_ids) <= 1 or len(chain_ids) > MAX_PLAUSIBLE_CHAIN:
        return [], None

    members = sorted(
        (by_id[cid] for cid in chain_ids if cid != cluster_id and cid in by_id),
        key=lambda c: c.first_seen_at,
    )

    # Anchor label: the non-backdrop, non-generic entity this cluster
    # shares with the most other chain members — display purposes only,
    # not used to re-derive membership.
    counts: Dict[str, int] = {}
    for cid in chain_ids:
        c = by_id.get(cid)
        if not c:
            continue
        for key, is_backdrop in c.entity_backdrop.items():
            if is_backdrop:
                continue
            counts[key] = counts.get(key, 0) + 1
    anchor = max(counts, key=counts.get) if counts else None
    anchor_display = anchor.split(":", 1)[1] if anchor and ":" in anchor else anchor

    return members, anchor_display
