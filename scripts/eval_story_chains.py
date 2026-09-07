"""Offline evaluation harness for story-timeline chaining ("story so far").

Second attempt at this feature. The first (backend/docs/story-graph-design.md,
scripts/experiment_story_edges.py) was built when clustering itself was
broken (98.5% singletons) and never got real quantitative validation — every
judgment was "does this look right" on one hand-eyeballed window. This
harness exists so the second attempt doesn't repeat that: any chaining
config is *scored* against a labelled pair set, same pattern as
eval_clustering.py's Phase 0.

What's carried over from the first attempt (validated by repeated manual
review there):
  - candidate generation by shared, canonicalized entity (Node 1)
  - genericity check via entity_stats.baseline_rate, falling back to
    in-set document frequency when baseline_rate is immature (Node 3)
  - the Round-5 "actor type" filter: locations are backdrop unconditionally,
    entities.backdrop (LLM-flagged), organizations matching a real Source
    name — never a hand-typed denylist

What's deliberately NOT carried over:
  - Node 7/8 (time-agnostic actor-exclusion sub-clustering + outlier-gap
    branching). This was the part that was never fixed — it silently
    dropped real chain members (the Brydon Carse #10354 case) and never
    got a validated replacement. With clustering fixed, a topic group's
    members are far more likely to already BE one coherent story in
    chronological order; there is no evidence yet that sub-splitting is
    still needed, so it isn't rebuilt speculatively. If real data proves
    otherwise, design it against labelled data via this harness, not before.
  - every threshold from the first attempt (MAX_DF_RATIO, SUBSUMPTION_RATIO,
    GENERIC_PERCENTILE) — re-derived here against current data, not carried
    over as inherited numbers.

Difference from eval_clustering.py's "same event" labelling: this labels
*continuation* — does cluster B represent a later development of the same
evolving story as cluster A — which is directional and cares about time.

Four modes, run in order:

    # 1. on the server (or anywhere with DATABASE_URL pointing at prod).
    #    No article bodies loaded — see check_timeline_readiness.py for the
    #    egress shape this pulls (~21MB/30d as of 2026-09-07).
    python scripts/eval_story_chains.py fetch --days 30 --out fixture.json

    # 2-3. locally, needs ANTHROPIC_API_KEY (~$0.20-1 one-time for labels)
    python scripts/eval_story_chains.py pairs --fixture fixture.json --out pairs.json
    python scripts/eval_story_chains.py label --pairs pairs.json --out labels.json

    # 4. free, repeat as often as you like
    python scripts/eval_story_chains.py grid --fixture fixture.json --labels labels.json

Metrics are pairwise: over every labelled pair, did the chain-builder put
the two clusters in the same final chain? Precision matters at least as
much as recall here too — a wrong continuation edge tells a user two
unrelated stories are one developing story, which is a worse failure than
a real continuation just not surfacing yet.
"""
import argparse
import asyncio
import itertools
import json
import os
import sys
from dataclasses import dataclass, asdict, replace
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.entity_graph import canonicalize_entity  # noqa: E402

LABEL_MODEL = "claude-haiku-4-5"

# A chain past this size in a genuine news window is a topic blob, not one
# story — same reasoning and same order of magnitude as eval_clustering.py's
# MAX_PLAUSIBLE_CLUSTER (60 articles/cluster there). A chain is clusters, not
# articles, so a real multi-week saga (a court case, an election) can
# legitimately run longer; this exists to catch "india" or "bjp" surviving
# the backdrop/generic filter and swallowing everything, not to cap real
# sagas tightly.
MAX_PLAUSIBLE_CHAIN = 40


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

async def _fetch(days: int, out_path: str) -> None:
    from sqlalchemy import text
    from app.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        clusters = (await session.execute(
            text(
                """
                SELECT id, headline, entities, first_seen_at, last_updated_at,
                       distinct_source_count
                FROM story_clusters
                WHERE first_seen_at >= now() - (:days || ' days')::interval
                ORDER BY first_seen_at ASC
                """
            ),
            {"days": str(days)},
        )).mappings().all()

        stats = (await session.execute(
            text("SELECT entity_key, baseline_rate FROM entity_stats")
        )).all()

        sources = (await session.execute(
            text("SELECT name FROM sources")
        )).scalars().all()

    out_clusters = [
        {
            "id": r["id"],
            "headline": r["headline"] or "",
            "entities": r["entities"] or {},
            "first_seen_at": r["first_seen_at"].isoformat() if r["first_seen_at"] else None,
            "last_updated_at": r["last_updated_at"].isoformat() if r["last_updated_at"] else None,
            "distinct_source_count": r["distinct_source_count"] or 1,
        }
        for r in clusters
        if r["first_seen_at"]
    ]
    baseline_rates = {k: v for k, v in stats}
    source_names = {s.strip().lower() for s in sources if s}

    with open(out_path, "w") as fh:
        json.dump(
            {
                "days": days,
                "clusters": out_clusters,
                "baseline_rates": baseline_rates,
                "source_names": sorted(source_names),
            },
            fh,
            indent=1,
        )
    print(f"Wrote {len(out_clusters)} clusters over {days}d to {out_path}")
    print(f"entity_stats rows: {len(baseline_rates)}  sources: {len(source_names)}")


@dataclass
class ClusterRec:
    id: int
    headline: str
    first_seen_at: datetime
    last_updated_at: datetime
    distinct_source_count: int
    # canonical entity key -> is it structurally backdrop (Round 5 signal),
    # independent of whether it turns out generic (Node 3 is a separate axis)
    entity_backdrop: Dict[str, bool]


def load_fixture(path: str) -> Tuple[List[ClusterRec], Dict[str, float], Set[str]]:
    with open(path) as fh:
        blob = json.load(fh)

    source_names: Set[str] = set(blob.get("source_names", []))
    baseline_rates: Dict[str, float] = blob.get("baseline_rates", {})

    clusters: List[ClusterRec] = []
    for c in blob["clusters"]:
        entities = c["entities"] or {}
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
                # An entity can appear via more than one raw spelling in the
                # same cluster; OR the signal so any positive hit sticks.
                entity_backdrop[key] = entity_backdrop.get(key, False) or is_backdrop

        clusters.append(ClusterRec(
            id=c["id"],
            headline=c["headline"],
            first_seen_at=datetime.fromisoformat(c["first_seen_at"]),
            last_updated_at=datetime.fromisoformat(c["last_updated_at"]),
            distinct_source_count=c["distinct_source_count"],
            entity_backdrop=entity_backdrop,
        ))

    clusters.sort(key=lambda c: c.first_seen_at)
    return clusters, baseline_rates, source_names


# ---------------------------------------------------------------------------
# Chain assembly
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Params:
    # Node 3: an entity is generic if its commonness is at/above this
    # percentile of the in-window distribution. Applied to whichever metric
    # is selected below.
    generic_percentile: float = 0.95
    # baseline_rate = entity_stats (mature, real long-run commonness).
    # in_set_df = fraction of in-window clusters mentioning it (fallback
    # for entities entity_stats hasn't matured on yet — see Node 3).
    generic_metric: str = "baseline_rate"
    # Round 5: reject location/backdrop/source-name entities as group-
    # forming keys regardless of genericity. Kept as a knob so the grid can
    # show whether it's actually earning its keep on real data, which the
    # first attempt never got to measure (see story-graph-design.md's
    # "Not yet validated").
    use_backdrop_filter: bool = True
    # Node 6b, adapted: a topic group >= this fraction contained in an
    # already-kept larger group is merged into it (union), not dropped —
    # dropping outright is what the first attempt's Node 7/8 got in trouble
    # for (a member that's only in the smaller group would vanish). Merging
    # preserves every member while still collapsing the duplicate label.
    #
    # A 2026-09-07 experiment tried replacing this whole group+subsumption
    # design with a pairwise entity-overlap graph + connected components,
    # requiring >=2 or >=3 shared entities per edge instead of Node 5's
    # "one is enough" — motivated by a false-positive analysis showing
    # single-shared-institutional-entity pairs (a specific court, a named
    # official) getting wrongly linked. Result: >=3 collapsed recall
    # (0.576->0.337) for a ~0.2pp precision gain; >=2 reintroduced Round
    # 2's original "topic bucket" drift in a new form (a 114-cluster
    # generic-European-football-roundup chain via club/player-name
    # transitivity, none of which individually looked generic enough to
    # filter). Neither beat this design on the full labelled set. Reverted;
    # institutional-entity false positives are a known, accepted
    # limitation for now rather than a solved one — see
    # backend/docs/story-graph-design.md for how this mirrors Round 2/4's
    # unresolved fragmentation issues in the first attempt.
    subsumption_ratio: float = 0.8
    # Institution/diffuseness filter (2026-09-07, second attempt at the
    # institutional-entity false-positive problem the min_shared experiment
    # above failed to fix): an entity whose occurrences pair with mostly
    # DIFFERENT, unrelated companion entities each time is acting as
    # connective tissue (a venue, a standing official, a prolific person's
    # many unrelated stories) rather than a story's actual subject — the
    # same underlying idea as "a place is backdrop, not what the story is
    # about" from Round 5, generalized to any entity type via a measurable
    # signal instead of a type check. See compute_institution_keys.
    use_institution_filter: bool = False
    institution_overlap_threshold: float = 0.15
    institution_min_postings: int = 3
    # Candidate window for pair generation / chain membership. Fixed by
    # the fixture in practice; kept as a field so it prints in the label.
    window_days: int = 30

    def label(self) -> str:
        inst = (f" inst<{self.institution_overlap_threshold:.2f}"
                if self.use_institution_filter else "")
        return (
            f"generic({self.generic_metric})>={self.generic_percentile:.2f} "
            f"backdrop={'on' if self.use_backdrop_filter else 'off'} "
            f"subsume>={self.subsumption_ratio:.2f}{inst}"
        )


def compute_institution_keys(clusters: List[ClusterRec], qualifying: List[Set[str]],
                              overlap_threshold: float, min_postings: int) -> Set[str]:
    """Entities behaving as connective tissue rather than a story's subject:
    their occurrences pair with mostly DIFFERENT companion entities each
    time, instead of recurring with the same few (as a real evolving
    story's cast would). Measured, not typed — catches standing
    institutions (a specific court, a sports board official) the same way
    it catches a prolific person whose many news items are genuinely
    unrelated stories, not one to fragment via Node 7/8 (deliberately not
    rebuilt — see build_chains' docstring). Entities with too few mentions
    to judge (< min_postings) are left alone — innocent until shown
    otherwise, not filtered on missing evidence.
    """
    postings: Dict[str, List[int]] = {}
    for i, keys in enumerate(qualifying):
        for key in keys:
            postings.setdefault(key, []).append(i)

    institutions: Set[str] = set()
    for key, idxs in postings.items():
        if len(idxs) < min_postings:
            continue
        companions = [qualifying[i] - {key} for i in idxs]
        overlaps = []
        for a in range(len(companions)):
            for b in range(a + 1, len(companions)):
                ca, cb = companions[a], companions[b]
                if not ca or not cb:
                    overlaps.append(0.0)
                    continue
                overlaps.append(len(ca & cb) / min(len(ca), len(cb)))
        avg_overlap = sum(overlaps) / len(overlaps) if overlaps else 0.0
        if avg_overlap < overlap_threshold:
            institutions.add(key)
    return institutions


def _generic_cutoff(values: List[float], percentile: float) -> float:
    if not values:
        return 1.0
    s = sorted(values)
    idx = min(int(len(s) * percentile), len(s) - 1)
    return s[idx]


def build_chains(clusters: List[ClusterRec], baseline_rates: Dict[str, float],
                  p: Params) -> Dict[int, Set[int]]:
    """Returns cluster index -> the set of cluster indices in its final chain
    (including itself). A cluster with no chain maps to {its own index}."""
    n = len(clusters)

    # In-set document frequency, needed either as the metric itself or as
    # the Node-3 fallback when baseline_rate hasn't seen an entity yet.
    doc_freq: Dict[str, int] = {}
    for c in clusters:
        for key in c.entity_backdrop:
            doc_freq[key] = doc_freq.get(key, 0) + 1
    in_set_df = {k: v / n for k, v in doc_freq.items()} if n else {}

    if p.generic_metric == "baseline_rate" and baseline_rates:
        metric_values = list(baseline_rates.values())
    else:
        metric_values = list(in_set_df.values())
    cutoff = _generic_cutoff(metric_values, p.generic_percentile)

    def is_generic(key: str) -> bool:
        if p.generic_metric == "baseline_rate" and key in baseline_rates:
            return baseline_rates[key] >= cutoff
        return in_set_df.get(key, 0.0) >= cutoff

    # Node 6, simplified: any entity key that survives backdrop+genericity
    # filtering forms a topic group of every cluster mentioning it. The
    # first attempt additionally picked ONE "actor" per candidate pair via
    # in-match frequency (Node 2) before this step — that machinery existed
    # to arbitrate between competing entities pairwise. Skipped here: once
    # backdrop/generic entities are excluded at the entity level, a
    # surviving specific entity is a valid group-forming key on its own,
    # and Node 6b below already collapses groups that turn out to describe
    # the same story under different labels.
    qualifying: List[Set[str]] = []
    for c in clusters:
        keys = set()
        for key, is_backdrop in c.entity_backdrop.items():
            if p.use_backdrop_filter and is_backdrop:
                continue
            if is_generic(key):
                continue
            keys.add(key)
        qualifying.append(keys)

    institutions: Set[str] = set()
    if p.use_institution_filter:
        institutions = compute_institution_keys(
            clusters, qualifying, p.institution_overlap_threshold, p.institution_min_postings,
        )

    groups: Dict[str, Set[int]] = {}
    for i, keys in enumerate(qualifying):
        for key in keys:
            if key in institutions:
                continue
            groups.setdefault(key, set()).add(i)

    # Node 6b, adapted to merge instead of drop (see Params.subsumption_ratio
    # docstring). Process largest-first so a small group merges into the
    # established larger one rather than the reverse.
    ordered = sorted(groups.values(), key=len, reverse=True)
    kept: List[Set[int]] = []
    for group in ordered:
        if len(group) < 2:
            continue  # a group of 1 has nothing to chain with
        merged = False
        for k in kept:
            overlap = len(group & k)
            if overlap / len(group) >= p.subsumption_ratio:
                k.update(group)
                merged = True
                break
        if not merged:
            kept.append(set(group))

    assignment: Dict[int, Set[int]] = {i: {i} for i in range(n)}
    for group in kept:
        for i in group:
            assignment[i] = group
    return assignment


def score(clusters: List[ClusterRec], baseline_rates: Dict[str, float],
          labels: Dict[str, str], id_to_idx: Dict[int, int], p: Params) -> dict:
    assignment = build_chains(clusters, baseline_rates, p)

    # same_event counts as a positive for chaining purposes (two clusters
    # describing one incident belong in the same timeline entry, arguably
    # more so than a genuine "continues" pair) but is tallied separately —
    # it's a downstream symptom of clustering's own recall gap (production
    # runs ~0.70 pairwise recall), not something the chain-builder should be
    # penalised or credited for as if it were a real continuation call.
    tp = fp = fn = tn = 0
    same_event_together = same_event_total = 0
    for key, verdict in labels.items():
        x, y = (int(v) for v in key.split("-"))
        if x not in id_to_idx or y not in id_to_idx:
            continue
        ix, iy = id_to_idx[x], id_to_idx[y]
        together = iy in assignment[ix]
        if verdict == "same_event":
            same_event_total += 1
            same_event_together += together
            continue
        if verdict == "continues":
            tp += together
            fn += not together
        else:
            fp += together
            tn += not together

    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    same_event_catch_rate = (
        same_event_together / same_event_total if same_event_total else None
    )

    chain_ids = {frozenset(v) for v in assignment.values() if len(v) > 1}
    sizes = [len(c) for c in chain_ids]

    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "chains": len(chain_ids),
        "chained_clusters": sum(sizes),
        "largest_chain": max(sizes) if sizes else 0,
        "same_event_catch_rate": same_event_catch_rate,
        "same_event_total": same_event_total,
    }


# ---------------------------------------------------------------------------
# Candidate pair generation for labelling
# ---------------------------------------------------------------------------

def _stratify(scored_pairs: List[Tuple[int, Tuple[int, int]]],
              max_pairs: int, bins: int = 6) -> List[Tuple[int, int]]:
    """Sample across the shared-entity-count range, not just the top-N —
    mirrors eval_clustering.py's _stratify for the same reason: labelling
    budget spent only on the most-obviously-related pairs teaches the grid
    nothing about the boundary."""
    import random

    rng = random.Random(20260907)
    buckets: Dict[int, List[Tuple[int, int]]] = {}
    for n_shared, pair in scored_pairs:
        idx = min(n_shared, bins - 1)
        buckets.setdefault(idx, []).append(pair)

    per_bucket = max(max_pairs // max(len(buckets), 1), 1)
    out: List[Tuple[int, int]] = []
    for idx in sorted(buckets):
        group = buckets[idx]
        rng.shuffle(group)
        out.extend(group[:per_bucket])
    return out


def _build_pairs(clusters: List[ClusterRec], baseline_rates: Dict[str, float],
                  window_days: float, negatives: int, max_pairs: int) -> List[Tuple[int, int]]:
    """Candidate pairs worth labelling: clusters sharing >=1 non-backdrop,
    non-generic entity within the time window, drawn with an EARLIER-then-
    LATER ordering since continuation is directional. A generous, permissive
    filter here (Params defaults) — the label set only needs to cover
    plausible candidates; the grid decides the real thresholds afterward."""
    default = Params()
    n = len(clusters)
    doc_freq: Dict[str, int] = {}
    for c in clusters:
        for key in c.entity_backdrop:
            doc_freq[key] = doc_freq.get(key, 0) + 1
    in_set_df = {k: v / n for k, v in doc_freq.items()} if n else {}
    metric_values = list(baseline_rates.values()) or list(in_set_df.values())
    cutoff = _generic_cutoff(metric_values, default.generic_percentile)

    def eligible_keys(c: ClusterRec) -> Set[str]:
        keys = set()
        for key, is_backdrop in c.entity_backdrop.items():
            if is_backdrop:
                continue
            rate = baseline_rates.get(key, in_set_df.get(key, 0.0))
            if rate >= cutoff:
                continue
            keys.add(key)
        return keys

    postings: Dict[str, List[int]] = {}
    for i, c in enumerate(clusters):
        for key in eligible_keys(c):
            postings.setdefault(key, []).append(i)

    window = timedelta(days=window_days)
    scored: Dict[Tuple[int, int], int] = {}
    for key, idxs in postings.items():
        for a_pos in range(len(idxs)):
            i = idxs[a_pos]
            for j in idxs[a_pos + 1:]:
                if clusters[j].first_seen_at - clusters[i].first_seen_at > window:
                    continue
                pair = (i, j)  # already earlier-then-later since clusters sorted
                scored[pair] = scored.get(pair, 0) + 1

    ranked = [(n_shared, pair) for pair, n_shared in scored.items()]
    pairs = set(_stratify(ranked, max_pairs))

    import random
    rng = random.Random(20260907)
    ids = list(range(n))
    while negatives > 0 and len(ids) > 1:
        x, y = rng.sample(ids, 2)
        i, j = (x, y) if clusters[x].first_seen_at <= clusters[y].first_seen_at else (y, x)
        if (i, j) not in pairs:
            pairs.add((i, j))
            negatives -= 1

    return sorted(pairs)


LABEL_SYSTEM = """You label pairs of Indian news story clusters for a story-timeline evaluation.

Each cluster is meant to be an already-corroborated news story (multiple
outlets covering one event), but clustering is imperfect: sometimes the
exact same specific event ends up split across two cluster IDs instead of
one. Your job is to tell apart three cases:

"same_event" — A and B report the SAME specific incident/announcement/
ruling/match/death, essentially duplicates that should have been one
cluster (e.g. two outlets' write-ups of one retirement announcement, one
death, one squad recall, filed the same day).

"continues" — B is a GENUINE LATER DEVELOPMENT of A's story: a distinct
subsequent incident in the same ongoing narrative (an update, a new ruling,
an arrest, a reaction, day 2 of a multi-day event) — not the same specific
incident, but a real next chapter a reader following A would want to see.

"unrelated" — neither of the above:
- the same person/organisation/place in a genuinely different story
- the same broad topic or category (e.g. both "Bollywood news", both
  "monsoon coverage") without being the same developing story
- routine recurring coverage that happens to share an entity (daily market
  wraps, separate unrelated matches by the same club/player) unless B is
  specifically about how A's story developed

Be strict. When genuinely uncertain between "continues" and "unrelated",
answer "unrelated". When genuinely uncertain whether it's the same event or
a later one, answer "same_event" only if they clearly describe one
incident, not two."""


def _label_prompt(pair_id: str, a: ClusterRec, b: ClusterRec) -> str:
    return (
        f"Pair {pair_id}\n\n"
        f"A) [{a.first_seen_at.date()}] {a.headline}\n\n"
        f"B) [{b.first_seen_at.date()}] {b.headline}\n\n"
        f'Reply with exactly one JSON object: {{"verdict": "same_event"}}, '
        f'{{"verdict": "continues"}}, or {{"verdict": "unrelated"}}'
    )


def _do_label(pairs_path: str, out_path: str, resume_batch_id: Optional[str] = None) -> None:
    try:
        import anthropic
    except ImportError:
        sys.exit("pip install anthropic  (see requirements-dev.txt)")

    client = anthropic.Anthropic()
    import time

    if resume_batch_id:
        # Reattach to an already-submitted batch instead of resubmitting —
        # a poll-loop network blip (ConnectTimeout on retrieve/results, seen
        # 2026-09-07) must not silently double the labelling job. Submission
        # itself already succeeded by the time polling can fail.
        batch = client.messages.batches.retrieve(resume_batch_id)
        print(f"Resuming batch {batch.id} (status={batch.processing_status}). Polling...", flush=True)
    else:
        with open(pairs_path) as fh:
            blob = json.load(fh)
        by_id = {c["id"]: c for c in blob["clusters"]}
        pairs = [tuple(p) for p in blob["pairs"]]

        def rec(d: dict) -> ClusterRec:
            return ClusterRec(
                id=d["id"], headline=d["headline"],
                first_seen_at=datetime.fromisoformat(d["first_seen_at"]),
                last_updated_at=datetime.fromisoformat(d["last_updated_at"]),
                distinct_source_count=d["distinct_source_count"],
                entity_backdrop={},
            )

        requests = [
            {
                "custom_id": f"{x}-{y}",
                "params": {
                    "model": LABEL_MODEL,
                    "max_tokens": 64,
                    "system": LABEL_SYSTEM,
                    "messages": [{"role": "user",
                                  "content": _label_prompt(f"{x}-{y}", rec(by_id[x]), rec(by_id[y]))}],
                },
            }
            for x, y in pairs if x in by_id and y in by_id
        ]

        print(f"Submitting {len(requests)} pairs to the Batch API ({LABEL_MODEL}, 50% off)...", flush=True)
        batch = client.messages.batches.create(requests=requests)
        print(f"Batch {batch.id} submitted. Polling...", flush=True)

    while True:
        try:
            batch = client.messages.batches.retrieve(batch.id)
        except anthropic.APIConnectionError as e:
            # Transient network blips must not lose the batch id — the job
            # keeps running server-side regardless of whether this process
            # can currently reach the API. Log which batch to resume and
            # keep retrying rather than crashing (2026-09-07 incident: a
            # ConnectTimeout here killed the process with no batch id in
            # the visible scrollback).
            print(f"  poll failed ({e}); retrying in 20s. "
                  f"If this process dies, resume with --resume-batch-id {batch.id}",
                  flush=True)
            time.sleep(20)
            continue
        if batch.processing_status == "ended":
            break
        print(f"  status={batch.processing_status} counts={batch.request_counts}", flush=True)
        time.sleep(20)

    labels: Dict[str, str] = {}
    failed = 0
    for attempt in range(5):
        try:
            results_iter = list(client.messages.batches.results(batch.id))
            break
        except anthropic.APIConnectionError as e:
            print(f"  results fetch failed ({e}); retrying "
                  f"(attempt {attempt + 1}/5). Batch id: {batch.id}", flush=True)
            time.sleep(10)
    else:
        sys.exit(f"Could not fetch results after 5 attempts. Batch {batch.id} "
                  f"is still done server-side — retry with --resume-batch-id {batch.id}.")

    for result in results_iter:
        if result.result.type != "succeeded":
            failed += 1
            continue
        text = "".join(b.text for b in result.result.message.content if b.type == "text")
        try:
            verdict = json.loads(text.strip().strip("`").removeprefix("json").strip())["verdict"]
        except Exception:
            if '"same_event"' in text:
                verdict = "same_event"
            elif '"continues"' in text:
                verdict = "continues"
            else:
                verdict = "unrelated"
        labels[result.custom_id] = verdict

    with open(out_path, "w") as fh:
        json.dump({"model": LABEL_MODEL, "labels": labels}, fh, indent=1)

    counts = {v: sum(1 for x in labels.values() if x == v)
              for v in ("same_event", "continues", "unrelated")}
    print(f"Wrote {len(labels)} labels to {out_path} "
          f"({counts['same_event']} same_event / {counts['continues']} continues / "
          f"{counts['unrelated']} unrelated, {failed} failed)")


# ---------------------------------------------------------------------------
# Grid + inspect
# ---------------------------------------------------------------------------

def _print_row(name: str, s: dict) -> None:
    se = (f" same_event_catch={s['same_event_catch_rate']:.3f}(n={s['same_event_total']})"
          if s.get("same_event_catch_rate") is not None else "")
    print(f"{name:<58} P={s['precision']:.3f} R={s['recall']:.3f} "
          f"F1={s['f1']:.3f}  chains={s['chains']:<5} "
          f"chained_clusters={s['chained_clusters']:<5} max={s['largest_chain']}{se}")


GRID = {
    "generic_percentile": [0.90, 0.95, 0.99],
    "generic_metric": ["baseline_rate", "in_set_df"],
    "use_backdrop_filter": [True, False],
    "subsumption_ratio": [0.6, 0.7, 0.8, 0.9],
    "use_institution_filter": [True, False],
    "institution_overlap_threshold": [0.10, 0.15, 0.20],
}


def _grid(clusters: List[ClusterRec], baseline_rates: Dict[str, float],
          labels: Dict[str, str], top: int, out_json: Optional[str]) -> None:
    id_to_idx = {c.id: i for i, c in enumerate(clusters)}

    baseline = score(clusters, baseline_rates, labels, id_to_idx, Params())
    print("\nDEFAULT PARAMS")
    _print_row(Params().label(), baseline)

    keys = list(GRID)
    configs = [Params(**dict(zip(keys, combo)))
               for combo in itertools.product(*(GRID[k] for k in keys))]

    print(f"\nGRID ({len(labels)} labelled pairs, {len(configs)} configs)")
    results = [(p, score(clusters, baseline_rates, labels, id_to_idx, p)) for p in configs]

    if out_json:
        with open(out_json, "w") as fh:
            json.dump([{"params": asdict(p), "label": p.label(), **s}
                       for p, s in results], fh, indent=1)
        print(f"(full results for all {len(results)} configs -> {out_json})")

    plausible = [(p, s) for p, s in results if s["largest_chain"] <= MAX_PLAUSIBLE_CHAIN]
    rejected = len(results) - len(plausible)
    plausible.sort(key=lambda t: (t[1]["precision"] * 2 + t[1]["recall"]), reverse=True)

    print(f"\n{rejected}/{len(results)} configs rejected for a chain larger than "
          f"{MAX_PLAUSIBLE_CHAIN} clusters (topic-blob failures).\n")
    for p, s in plausible[:top]:
        _print_row(p.label(), s)

    if not plausible:
        print("No config passed the blob filter — loosen MAX_PLAUSIBLE_CHAIN "
              "or tighten the grid.")
        return
    best, best_s = plausible[0]
    print("\nBEST CONFIG")
    print(json.dumps(asdict(best), indent=2))
    print(f"\nvs default: precision {baseline['precision']:.3f} -> {best_s['precision']:.3f}, "
          f"recall {baseline['recall']:.3f} -> {best_s['recall']:.3f}")


def _inspect(clusters: List[ClusterRec], baseline_rates: Dict[str, float],
             p: Params, limit: int, min_size: int) -> None:
    """Print the chains a config produces, largest first. No labels needed —
    catches blob failures (one entity surviving the filters and swallowing
    unrelated stories) before spending any labelling budget."""
    assignment = build_chains(clusters, baseline_rates, p)
    chain_sets = {frozenset(v) for v in assignment.values() if len(v) >= min_size}
    chains = sorted(chain_sets, key=len, reverse=True)

    n = len(clusters)
    chained = sum(len(c) for c in chain_sets)
    print(f"\nCONFIG  {p.label()}")
    print(f"clusters={n}  chains={len(chain_sets)}  chained_clusters={chained} "
          f"({chained / n:.1%})" if n else "no clusters")

    print(f"\n--- {min(limit, len(chains))} largest chains (eyeball for blob failures) ---")
    for group in chains[:limit]:
        members = sorted((clusters[i] for i in group), key=lambda c: c.first_seen_at)
        print(f"\n[{len(members)} clusters]")
        for c in members[:8]:
            print(f"   ({c.first_seen_at.date()}) {c.headline[:96]}")
        if len(members) > 8:
            print(f"   ... +{len(members) - 8} more")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="mode", required=True)

    f = sub.add_parser("fetch", help="pull a real cluster fixture from the DB")
    f.add_argument("--days", type=int, default=30)
    f.add_argument("--out", default="fixture.json")

    pr = sub.add_parser("pairs", help="pick candidate pairs worth labelling")
    pr.add_argument("--fixture", required=True)
    pr.add_argument("--out", default="pairs.json")
    pr.add_argument("--window-days", type=float, default=30.0)
    pr.add_argument("--negatives", type=int, default=100)
    pr.add_argument("--max-pairs", type=int, default=600)

    lb = sub.add_parser("label", help="label pairs via the Haiku Batch API")
    lb.add_argument("--pairs", help="required unless --resume-batch-id is given")
    lb.add_argument("--out", default="labels.json")
    lb.add_argument("--resume-batch-id",
                    help="reattach to an already-submitted batch instead of "
                         "resubmitting, e.g. after a local network blip "
                         "killed the polling loop (--pairs is ignored)")

    gr = sub.add_parser("grid", help="sweep configs and rank them")
    gr.add_argument("--fixture", required=True)
    gr.add_argument("--labels", required=True)
    gr.add_argument("--top", type=int, default=15)
    gr.add_argument("--out-json", help="dump every config's scores for re-ranking")

    ins = sub.add_parser("inspect", help="show a config's chains (no labels/API needed)")
    ins.add_argument("--fixture", required=True)
    ins.add_argument("--limit", type=int, default=12)
    ins.add_argument("--min-size", type=int, default=2)
    ins.add_argument("--generic-percentile", type=float)
    ins.add_argument("--generic-metric", choices=["baseline_rate", "in_set_df"])
    ins.add_argument("--subsumption-ratio", type=float)
    ins.add_argument("--no-backdrop-filter", action="store_true")
    ins.add_argument("--institution-filter", action="store_true",
                     help="enable the institution/diffuseness filter (off by "
                          "default here; production defaults it on at 0.15)")
    ins.add_argument("--institution-overlap-threshold", type=float)

    args = ap.parse_args()

    if args.mode == "fetch":
        asyncio.run(_fetch(args.days, args.out))
        return

    if args.mode == "pairs":
        clusters, baseline_rates, _ = load_fixture(args.fixture)
        pairs = _build_pairs(clusters, baseline_rates, args.window_days,
                             args.negatives, args.max_pairs)
        slim = [
            {
                "id": c.id, "headline": c.headline,
                "first_seen_at": c.first_seen_at.isoformat(),
                "last_updated_at": c.last_updated_at.isoformat(),
                "distinct_source_count": c.distinct_source_count,
            }
            for c in clusters
        ]
        # Pairs are stored as cluster IDS, not fixture positions, so pairs.json
        # stays valid if load order ever changes.
        id_pairs = [(clusters[i].id, clusters[j].id) for i, j in pairs]
        with open(args.out, "w") as fh:
            json.dump({"pairs": id_pairs, "clusters": slim}, fh, indent=1)
        print(f"Wrote {len(id_pairs)} candidate pairs to {args.out}")
        print(f"Estimated labelling cost: ~${len(id_pairs) * 0.0004:.2f} "
              f"({LABEL_MODEL} via Batch API)")
        return

    if args.mode == "label":
        if not args.resume_batch_id and not args.pairs:
            sys.exit("--pairs is required unless --resume-batch-id is given")
        _do_label(args.pairs, args.out, resume_batch_id=args.resume_batch_id)
        return

    if args.mode == "inspect":
        clusters, baseline_rates, _ = load_fixture(args.fixture)
        overrides = {
            k: v for k, v in (
                ("generic_percentile", args.generic_percentile),
                ("generic_metric", args.generic_metric),
                ("subsumption_ratio", args.subsumption_ratio),
                ("institution_overlap_threshold", args.institution_overlap_threshold),
            ) if v is not None
        }
        if args.no_backdrop_filter:
            overrides["use_backdrop_filter"] = False
        if args.institution_filter:
            overrides["use_institution_filter"] = True
        p = replace(Params(), **overrides)
        print(f"RESOLVED  {p.label()}")
        _inspect(clusters, baseline_rates, p, args.limit, args.min_size)
        return

    clusters, baseline_rates, _ = load_fixture(args.fixture)
    with open(args.labels) as fh:
        labels = json.load(fh)["labels"]
    _grid(clusters, baseline_rates, labels, args.top, args.out_json)


if __name__ == "__main__":
    main()
