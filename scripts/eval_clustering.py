"""Offline evaluation harness for the story-clustering algorithm.

Phase 0 of the clustering rework. Nothing in the rework is trustworthy without
this: the current thresholds (SimHash <= 18, Jaccard >= 0.25 over title+snippet)
were each calibrated against a handful of hand-eyeballed examples, and together
they produce a 98.5% singleton rate in production. This script replays the
clustering algorithm offline over a real article fixture with configurable
parameters, and scores it against an LLM-labelled ground-truth pair set, so the
replacement thresholds are *chosen* rather than asserted.

Deliberately does NOT import the matching logic from poller.py: that logic is
what's being evaluated, and it's entangled with DB writes. The replay below
mirrors poller.ingest_source()'s matching loop (poller.py:297-337) with every
hard-coded decision turned into a knob. `CURRENT_PARAMS` reproduces production
behaviour and is the baseline every candidate config must beat.

Four modes, run in order:

    # 1. on the server (or anywhere with DATABASE_URL pointing at prod)
    python scripts/eval_clustering.py fetch --days 3 --out fixture.json

    # 2-3. locally, needs ANTHROPIC_API_KEY (~$1 one-time for the labels)
    python scripts/eval_clustering.py pairs  --fixture fixture.json --out pairs.json
    python scripts/eval_clustering.py label  --pairs pairs.json --out labels.json

    # 4. free, repeat as often as you like
    python scripts/eval_clustering.py grid   --fixture fixture.json --labels labels.json

Metrics are *pairwise*: over every labelled pair, did the replay put the two
articles in the same cluster? Precision is weighted above recall in the grid
ranking, because a false merge is worse than a miss here — a wrongly merged
cluster produces a confidently wrong framing comparison shown to the user,
whereas a missed merge only leaves a story as a singleton.
"""
import argparse
import asyncio
import itertools
import json
import os
import sys
from dataclasses import dataclass, asdict, replace
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence, Set, Tuple

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.dedup import (  # noqa: E402
    STOPWORDS,
    compute_simhash,
    hamming_distance,
    normalize_text,
)

# Mirrors poller.GEOGRAPHIC_CATEGORIES. Imported lazily in _geo_categories() so
# `pairs`/`label`/`grid` still run in an environment without the app settings
# (no DATABASE_URL, no API keys) — only `fetch` truly needs the app package.
_GEO_FALLBACK = frozenset({
    "northeast", "regional_south", "regional_west",
    "regional_east", "regional_north", "regional_central",
})

LABEL_MODEL = "claude-haiku-4-5"

# Largest cluster a config may produce before it is treated as a blob-merge
# failure rather than a tuning point. Calibrated on the fixture: the biggest
# genuine story across three days (the Indus Waters ruling) drew ~20 articles
# from 15 outlets, so a triple-digit "cluster" is always a collapse.
MAX_PLAUSIBLE_CLUSTER = 60


def _geo_categories() -> frozenset:
    try:
        from app.services.poller import GEOGRAPHIC_CATEGORIES
        return frozenset(GEOGRAPHIC_CATEGORIES)
    except Exception:
        return _GEO_FALLBACK


# ---------------------------------------------------------------------------
# Similarity primitives
#
# significant_tokens() in dedup.py folds title and snippet together. That fold
# is defect #1 in the plan: snippets are long and outlet-specific, so the
# Jaccard union denominator explodes while the intersection stays roughly
# title-sized. These variants let the grid measure title-only and
# overlap-coefficient scoring against it.
# ---------------------------------------------------------------------------

def tokens_of(text: Optional[str]) -> Set[str]:
    if not text:
        return set()
    return {
        tok for tok in normalize_text(text).split()
        if len(tok) >= 4 and tok not in STOPWORDS
    }


def jaccard(a: Set[str], b: Set[str]) -> float:
    if not a or not b:
        return 0.0
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def overlap_coefficient(a: Set[str], b: Set[str]) -> float:
    """|A n B| / min(|A|,|B|) -- unlike Jaccard, does not punish a pair for one
    side simply being wordier, which is exactly the asymmetry that sinks
    title+snippet Jaccard when a wire brief meets a full writeup."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def idf_overlap(a: Set[str], b: Set[str], idf: Dict[str, float]) -> float:
    """Overlap coefficient weighted by inverse document frequency.

    Plain token overlap treats every shared word as equally meaningful, which
    templated headlines exploit: Yahoo Finance's "<Company> (TICK) Q2 2026
    Earnings Call Transcript" and Bloomberg's "<Name>, <Company>: Profile and
    Biography" share nearly all their significant tokens while being about
    completely different subjects, and the observed run fused 31 and 29 of them
    respectively into single clusters. Weighting by IDF makes the boilerplate
    ("earnings", "transcript", "profile", "recruitment") contribute almost
    nothing, while rare tokens ("indus", "larak", "arbitration") carry the
    match — which is what actually distinguishes one story from another.
    """
    if not a or not b:
        return 0.0
    wa = sum(idf.get(t, 1.0) for t in a)
    wb = sum(idf.get(t, 1.0) for t in b)
    shared = sum(idf.get(t, 1.0) for t in a & b)
    denom = min(wa, wb)
    return shared / denom if denom else 0.0


def compute_idf(articles: Sequence[dict], field: str) -> Dict[str, float]:
    import math
    df: Dict[str, int] = {}
    for a in articles:
        for tok in a[field]:
            df[tok] = df.get(tok, 0) + 1
    n = len(articles)
    return {tok: math.log(n / (1 + c)) for tok, c in df.items()}


@dataclass(frozen=True)
class Params:
    """Every hard-coded clustering decision in poller.py, as a knob."""
    # Stage 1: SimHash prefilter
    use_simhash: bool = True
    simhash_max_distance: int = 18
    # Stage 2: content-word confirmation
    metric: str = "jaccard"           # "jaccard" | "overlap" | "idf_overlap"
    fields: str = "title_snippet"     # "title_snippet" | "title"
    threshold: float = 0.25
    min_shared: int = 2
    # Two articles from one outlet are never a framing contrast, and templated
    # single-source feeds (earnings transcripts, job listings) are the dominant
    # false-merge mode observed on real data.
    same_source_guard: bool = False
    # Stage 3: candidate selection
    #
    # use_blocking is the crux of defect #3. Production has NO blocking index:
    # the `candidate_limit` most-recently-updated clusters ARE the entire
    # candidate pool (poller.py:112-115), which at ~4,700 articles/day is
    # roughly the last half hour. With blocking on, candidates are drawn by
    # shared content tokens instead, so an outlet publishing the same story
    # hours later can still be found.
    use_blocking: bool = False
    window_hours: Optional[float] = None   # None => no time bound (prod today)
    candidate_limit: Optional[int] = 100   # None => unbounded
    compare_all_members: bool = False      # False => representative only (prod)
    # Stage 4: guards
    geo_guard: bool = True

    def label(self) -> str:
        bits = [
            f"simhash{'≤' + str(self.simhash_max_distance) if self.use_simhash else '-off'}",
            f"{self.metric}/{self.fields}≥{self.threshold}",
            f"shared≥{self.min_shared}",
            "blocked" if self.use_blocking else f"recent{self.candidate_limit or '∞'}",
            f"win{self.window_hours or '∞'}h",
            "all-members" if self.compare_all_members else "rep-only",
            "src-guard" if self.same_source_guard else "",
        ]
        bits = [b for b in bits if b]
        return " ".join(bits)


# Reproduces production behaviour BEFORE the 2026-09-02 clustering rework.
# Kept as the historical baseline every candidate had to beat — it is no
# longer what production runs.
CURRENT_PARAMS = Params()

# What production ACTUALLY runs, read from source (app/services/dedup.py
# MIN_TITLE_JACCARD/MIN_SHARED_TOKENS, app/services/poller.py
# CLUSTER_MATCH_WINDOW and its matching loop), not from the handoff doc.
#
# This exists because `CURRENT_PARAMS` silently stopped describing production
# the moment the rework shipped, so a `grid` run reported its baseline against
# an algorithm nobody was using. Any change to the constants above must be
# mirrored here, or this harness quietly measures fiction.
SHIPPED_PARAMS = Params(
    use_simhash=False,          # simhash dropped as a gate by the rework
    metric="jaccard",
    fields="title",             # title tokens only, not title+snippet
    threshold=0.40,             # dedup.MIN_TITLE_JACCARD
    min_shared=2,               # dedup.MIN_SHARED_TOKENS
    same_source_guard=True,     # poller skips same-source members
    use_blocking=True,          # cluster_tokens index
    window_hours=48.0,          # poller.CLUSTER_MATCH_WINDOW
    candidate_limit=None,
    compare_all_members=True,
    geo_guard=True,
)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

async def _fetch(days: int, out_path: str) -> None:
    from sqlalchemy import text
    from app.database import AsyncSessionLocal

    query = text(
        """
        SELECT a.id, a.title, a.snippet, a.published_at, a.simhash,
               a.source_id, s.name AS source_name, s.category AS source_category,
               a.cluster_id
        FROM articles a
        JOIN sources s ON s.id = a.source_id
        WHERE a.published_at >= now() - (:days || ' days')::interval
        ORDER BY a.published_at ASC
        """
    )
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(query, {"days": str(days)})).mappings().all()

    articles = [
        {
            "id": r["id"],
            "title": r["title"],
            "snippet": r["snippet"],
            "published_at": r["published_at"].isoformat() if r["published_at"] else None,
            "simhash": r["simhash"],
            "source_id": r["source_id"],
            "source_name": r["source_name"],
            "source_category": r["source_category"],
            "prod_cluster_id": r["cluster_id"],
        }
        for r in rows
        if r["title"] and r["published_at"]
    ]
    with open(out_path, "w") as fh:
        json.dump({"days": days, "articles": articles}, fh, indent=1)

    prod_clusters = {a["prod_cluster_id"] for a in articles}
    print(f"Wrote {len(articles)} articles over {days}d to {out_path}")
    print(f"Production grouped them into {len(prod_clusters)} clusters "
          f"({len(articles) / max(len(prod_clusters), 1):.2f} articles/cluster)")


def load_fixture(path: str) -> List[dict]:
    with open(path) as fh:
        articles = json.load(fh)["articles"]
    for a in articles:
        a["_dt"] = datetime.fromisoformat(a["published_at"])
        a["_tok_title"] = tokens_of(a["title"])
        a["_tok_both"] = tokens_of(f"{a['title']} {a.get('snippet') or ''}")
        if not a.get("simhash"):
            a["simhash"] = compute_simhash(a["title"], a.get("snippet"))
    articles.sort(key=lambda a: a["_dt"])
    return articles


# ---------------------------------------------------------------------------
# Candidate pair generation for labelling
# ---------------------------------------------------------------------------

# A token appearing in more than this fraction of documents ("india",
# "minister", "government") is useless as a blocking key: its posting list is
# enormous and any pair matched through it alone is exactly the coincidental
# overlap that shares_topic() exists to reject. Skipping them is what keeps
# both the pair builder and the replay near-linear instead of O(n^2) — and it
# is the same trick the production query will need (plan defect #3).
MAX_BLOCKING_DF = 0.02


def _blocking_index(articles: Sequence[dict], field: str) -> Tuple[Dict[str, List[int]], Set[str]]:
    """token -> article positions, plus the set of too-common tokens to skip."""
    postings: Dict[str, List[int]] = {}
    for i, a in enumerate(articles):
        for tok in a[field]:
            postings.setdefault(tok, []).append(i)
    cap = max(int(len(articles) * MAX_BLOCKING_DF), 50)
    common = {tok for tok, lst in postings.items() if len(lst) > cap}
    return postings, common


def _stratify(scored_pairs: List[Tuple[float, Tuple[int, int]]],
              max_pairs: int, bins: int = 10) -> List[Tuple[int, int]]:
    """Sample evenly across the similarity range instead of taking the top N.

    Labelling budget spent entirely on high-overlap pairs would tell us nothing
    about where the threshold should sit — the boundary region is the whole
    point. Bucketing by overlap score and drawing equally from each bucket
    keeps the cheap/ambiguous/obvious bands all represented.
    """
    import random

    rng = random.Random(20260902)
    buckets: Dict[int, List[Tuple[int, int]]] = {}
    for score, pair in scored_pairs:
        idx = min(int(score * bins), bins - 1)
        buckets.setdefault(idx, []).append(pair)

    per_bucket = max(max_pairs // max(len(buckets), 1), 1)
    out: List[Tuple[int, int]] = []
    for idx in sorted(buckets):
        group = buckets[idx]
        rng.shuffle(group)
        out.extend(group[:per_bucket])
    return out


def _build_pairs(articles: List[dict], top_k: int, window_hours: float,
                 negatives: int, max_pairs: int) -> List[Tuple[int, int]]:
    """Pairs worth spending labelling budget on.

    Nearly all random pairs are trivial negatives and teach the grid nothing.
    What matters is the decision boundary, so take each article's top-K
    title-overlap neighbours within a time window — these straddle the
    threshold. A slice of random pairs is added purely as a precision sanity
    check (a config that calls those "same story" is broken).
    """
    import random
    from collections import Counter

    best: Dict[Tuple[int, int], float] = {}
    window = timedelta(hours=window_hours)
    postings, common = _blocking_index(articles, "_tok_title")

    for i, a in enumerate(articles):
        shared: Counter = Counter()
        for tok in a["_tok_title"]:
            if tok in common:
                continue
            for j in postings.get(tok, ()):
                if j > i:
                    shared[j] += 1

        scored: List[Tuple[float, int]] = []
        for j, n in shared.items():
            if n < 2:
                continue
            b = articles[j]
            if b["_dt"] - a["_dt"] > window:
                continue
            if a["source_id"] == b["source_id"]:
                continue  # same outlet: not a framing contrast, skip
            score = overlap_coefficient(a["_tok_title"], b["_tok_title"])
            if score > 0:
                scored.append((score, j))
        scored.sort(reverse=True)
        for sc, j in scored[:top_k]:
            key = (min(a["id"], articles[j]["id"]), max(a["id"], articles[j]["id"]))
            best[key] = max(best.get(key, 0.0), sc)

    pairs = set(_stratify([(sc, k) for k, sc in best.items()], max_pairs))

    rng = random.Random(20260902)
    ids = [a["id"] for a in articles]
    while negatives > 0 and len(ids) > 1:
        x, y = rng.sample(ids, 2)
        key = (min(x, y), max(x, y))
        if key not in pairs:
            pairs.add(key)
            negatives -= 1

    return sorted(pairs)


LABEL_SYSTEM = """You label pairs of Indian news headlines for a clustering evaluation.

For each pair, decide whether both articles report THE SAME SPECIFIC NEWS EVENT —
the same incident, announcement, match, ruling, or development, such that a reader
would consider them two outlets covering one story.

Answer "same" only for the same underlying event. Answer "different" for:
- the same broad topic or ongoing saga, but distinct events or developments
- the same people or organisations in unrelated news
- a follow-up, reaction, or analysis piece about a different angle on the same day
- routine recurring coverage (daily market wraps, separate matches in one series)

Be strict. When genuinely uncertain, answer "different"."""


def _label_prompt(pair_id: str, a: dict, b: dict) -> str:
    return (
        f"Pair {pair_id}\n\n"
        f"A) [{a['source_name']}] {a['title']}\n"
        f"   {(a.get('snippet') or '')[:300]}\n\n"
        f"B) [{b['source_name']}] {b['title']}\n"
        f"   {(b.get('snippet') or '')[:300]}\n\n"
        f'Reply with exactly one JSON object: {{"verdict": "same"}} or {{"verdict": "different"}}'
    )


def _do_label(pairs_path: str, out_path: str) -> None:
    try:
        import anthropic
    except ImportError:
        sys.exit("pip install anthropic  (see requirements-dev.txt)")

    with open(pairs_path) as fh:
        blob = json.load(fh)
    by_id = {a["id"]: a for a in blob["articles"]}
    pairs = [tuple(p) for p in blob["pairs"]]

    client = anthropic.Anthropic()
    requests = [
        {
            "custom_id": f"{x}-{y}",
            "params": {
                "model": LABEL_MODEL,
                "max_tokens": 64,
                "system": LABEL_SYSTEM,
                "messages": [{"role": "user",
                              "content": _label_prompt(f"{x}-{y}", by_id[x], by_id[y])}],
            },
        }
        for x, y in pairs if x in by_id and y in by_id
    ]

    print(f"Submitting {len(requests)} pairs to the Batch API ({LABEL_MODEL}, 50% off)...", flush=True)
    batch = client.messages.batches.create(requests=requests)
    print(f"Batch {batch.id} submitted. Polling...", flush=True)

    import time
    while True:
        batch = client.messages.batches.retrieve(batch.id)
        if batch.processing_status == "ended":
            break
        # flush=True: this script is usually run detached (the batch can take
        # many minutes), and without it Python block-buffers stdout when it
        # isn't a TTY, so the log stays empty until the process exits and
        # there's no way to tell progress from a hang.
        print(f"  status={batch.processing_status} counts={batch.request_counts}",
              flush=True)
        time.sleep(20)

    labels: Dict[str, str] = {}
    failed = 0
    # Results arrive in arbitrary order -- key by custom_id, never by position.
    for result in client.messages.batches.results(batch.id):
        if result.result.type != "succeeded":
            failed += 1
            continue
        text = "".join(b.text for b in result.result.message.content if b.type == "text")
        try:
            verdict = json.loads(text.strip().strip("`").removeprefix("json").strip())["verdict"]
        except Exception:
            verdict = "same" if '"same"' in text else "different"
        labels[result.custom_id] = verdict

    with open(out_path, "w") as fh:
        json.dump({"model": LABEL_MODEL, "labels": labels}, fh, indent=1)

    same = sum(1 for v in labels.values() if v == "same")
    print(f"Wrote {len(labels)} labels to {out_path} ({same} same / "
          f"{len(labels) - same} different, {failed} failed)")


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------

@dataclass
class _Cluster:
    members: List[dict]

    @property
    def rep(self) -> dict:
        return self.members[0]

    @property
    def last_dt(self) -> datetime:
        return self.members[-1]["_dt"]


def _confirms(a: dict, b: dict, p: Params, idf: Dict[str, float]) -> bool:
    key = "_tok_title" if p.fields == "title" else "_tok_both"
    ta, tb = a[key], b[key]
    if not ta or not tb:
        return False
    if len(ta & tb) < p.min_shared:
        return False
    if p.same_source_guard and a["source_id"] == b["source_id"]:
        return False
    if p.metric == "overlap":
        score = overlap_coefficient(ta, tb)
    elif p.metric == "idf_overlap":
        score = idf_overlap(ta, tb, idf)
    else:
        score = jaccard(ta, tb)
    return score >= p.threshold


def replay(articles: List[dict], p: Params) -> Dict[int, int]:
    """Mirror of poller.py's matching loop. Returns article_id -> cluster index.

    Processes in published_at order across all sources. Production instead
    processes one feed at a time, re-reading the 100 most recent clusters per
    source; that ordering detail is itself part of defect #3, and a global
    chronological replay is the fairer common ground for comparing configs.
    """
    from collections import Counter

    geo = _geo_categories()
    field = "_tok_title" if p.fields == "title" else "_tok_both"
    clusters: List[_Cluster] = []
    assignment: Dict[int, int] = {}

    # Inverted index over the token field this config actually compares on, so
    # candidate lookup is proportional to the postings hit rather than to the
    # number of live clusters. Common tokens are excluded as blocking keys (see
    # MAX_BLOCKING_DF): a pair sharing only those is precisely the coincidental
    # overlap the confirmation stage is meant to reject.
    _, common = _blocking_index(articles, field)
    idf = compute_idf(articles, field)
    postings: Dict[str, Set[int]] = {}

    # Most-recently-touched-first list of cluster indices, mirroring how
    # poller.py re-reads `ORDER BY last_updated_at DESC LIMIT 100` per source
    # and pushes new clusters onto the head (poller.py:411). Stale duplicates
    # are tolerated and skipped during the scan, exactly as the real drifting
    # in-memory list behaves.
    recent: List[int] = []

    def _index(cluster_idx: int, member: dict) -> None:
        recent.insert(0, cluster_idx)
        for tok in member[field]:
            if tok not in common:
                postings.setdefault(tok, set()).add(cluster_idx)

    for art in articles:
        candidates: List[Tuple[int, _Cluster]] = []

        if p.use_blocking:
            hits: Counter = Counter()
            for tok in art[field]:
                if tok in common:
                    continue
                for idx in postings.get(tok, ()):
                    hits[idx] += 1
            for idx, n in hits.items():
                if n < p.min_shared:
                    continue
                candidates.append((idx, clusters[idx]))
            candidates.sort(key=lambda t: t[1].last_dt, reverse=True)
        else:
            # Production: the N most-recently-updated clusters are the whole
            # pool, with no content-based narrowing at all.
            seen: Set[int] = set()
            for idx in recent:
                if idx in seen:
                    continue
                seen.add(idx)
                candidates.append((idx, clusters[idx]))
                if p.candidate_limit is not None and len(candidates) >= p.candidate_limit:
                    break

        if p.window_hours is not None:
            cutoff = art["_dt"] - timedelta(hours=p.window_hours)
            candidates = [c for c in candidates if c[1].last_dt >= cutoff]
        if p.use_blocking and p.candidate_limit is not None:
            candidates = candidates[: p.candidate_limit]

        matched: Optional[int] = None
        for idx, cl in candidates:
            targets = cl.members if p.compare_all_members else [cl.rep]
            for target in targets:
                if p.use_simhash:
                    if not (art["simhash"] and target["simhash"]):
                        continue
                    if hamming_distance(art["simhash"], target["simhash"]) > p.simhash_max_distance:
                        continue
                if not _confirms(art, target, p, idf):
                    continue
                if p.geo_guard:
                    ca, cb = art["source_category"], target["source_category"]
                    if ca in geo and cb in geo and ca != cb:
                        continue
                matched = idx
                break
            if matched is not None:
                break

        if matched is None:
            clusters.append(_Cluster(members=[art]))
            matched = len(clusters) - 1
        else:
            clusters[matched].members.append(art)
        assignment[art["id"]] = matched
        _index(matched, art)

    return assignment


def score(articles: List[dict], labels: Dict[str, str], p: Params) -> dict:
    assignment = replay(articles, p)

    tp = fp = fn = tn = 0
    for key, verdict in labels.items():
        x, y = (int(v) for v in key.split("-"))
        if x not in assignment or y not in assignment:
            continue
        together = assignment[x] == assignment[y]
        if verdict == "same":
            tp += together
            fn += not together
        else:
            fp += together
            tn += not together

    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    sizes: Dict[int, int] = {}
    for cid in assignment.values():
        sizes[cid] = sizes.get(cid, 0) + 1
    singletons = sum(1 for n in sizes.values() if n == 1)

    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "clusters": len(sizes),
        "singleton_rate": singletons / len(sizes) if sizes else 0.0,
        "largest_cluster": max(sizes.values()) if sizes else 0,
    }


def _print_row(name: str, s: dict) -> None:
    print(f"{name:<58} P={s['precision']:.3f} R={s['recall']:.3f} "
          f"F1={s['f1']:.3f}  single={s['singleton_rate']:.1%} "
          f"clusters={s['clusters']:<6} max={s['largest_cluster']}")


def _inspect(articles: List[dict], p: Params, limit: int, min_size: int) -> None:
    """Print the multi-article clusters a config produces, largest first.

    Costs nothing and needs no labels, so it works while the API has no credit.
    Reading real merges is also the fastest way to catch the failure the label
    set is meant to guard against: a config that boosts recall by fusing
    everything about one entity into a single blob (all "Modi" stories, all
    "Sensex" wraps) looks great on singleton rate and is useless in the feed.
    """
    from collections import Counter as _Counter

    assignment = replay(articles, p)
    by_cluster: Dict[int, List[dict]] = {}
    for art in articles:
        by_cluster.setdefault(assignment[art["id"]], []).append(art)

    sizes = _Counter(len(v) for v in by_cluster.values())
    total = len(by_cluster)
    multi = [v for v in by_cluster.values() if len(v) >= min_size]
    multi.sort(key=len, reverse=True)

    print(f"\nCONFIG  {p.label()}")
    print(f"clusters={total}  singleton={sizes[1] / total:.1%}  "
          f"multi-article={total - sizes[1]}  "
          f"multi-source={sum(1 for v in by_cluster.values() if len({a['source_id'] for a in v}) >= 2)}")
    print(f"size dist: {dict(sorted(sizes.items())[:10])}")

    print(f"\n--- {min(limit, len(multi))} largest clusters (eyeball for false merges) ---")
    for group in multi[:limit]:
        sources = {a["source_id"] for a in group}
        print(f"\n[{len(group)} articles / {len(sources)} sources]")
        for a in group[:6]:
            print(f"   ({a['source_name'][:22]:<22}) {a['title'][:96]}")
        if len(group) > 6:
            print(f"   ... +{len(group) - 6} more")


GRID = {
    # The SimHash veto is defect #2: kept as a knob so the grid can show
    # whether retaining it at any threshold beats dropping it outright.
    "use_simhash": [True, False],
    "simhash_max_distance": [18, 30],
    "metric": ["jaccard", "overlap", "idf_overlap"],
    "fields": ["title_snippet", "title"],
    "threshold": [0.3, 0.4, 0.5, 0.6],
    "min_shared": [2, 3],
    "same_source_guard": [True, False],
    # Fixed across the sweep — these are the structural fixes (defects #3/#4),
    # not tuning choices, and the inspect runs already showed them to be
    # strictly better than the production equivalents.
    "use_blocking": [True],
    "window_hours": [48.0],
    "candidate_limit": [None],
    "compare_all_members": [True],
}


def _score_one(args_tuple):
    """Top-level for multiprocessing (closures aren't picklable)."""
    articles, labels, p = args_tuple
    return p, score(articles, labels, p)


def _grid(articles: List[dict], labels: Dict[str, str], top: int,
          out_json: Optional[str] = None) -> None:
    base = score(articles, labels, CURRENT_PARAMS)
    print("\nBASELINE (pre-rework, historical)")
    _print_row(CURRENT_PARAMS.label(), base)

    shipped = score(articles, labels, SHIPPED_PARAMS)
    print("\nSHIPPED (what production runs today)")
    _print_row(SHIPPED_PARAMS.label(), shipped)

    keys = list(GRID)
    configs = []
    for combo in itertools.product(*(GRID[k] for k in keys)):
        cfg = dict(zip(keys, combo))
        if not cfg["use_simhash"] and cfg["simhash_max_distance"] != 18:
            continue  # distance is meaningless with the prefilter off
        configs.append(replace(CURRENT_PARAMS, **cfg))

    print(f"\nGRID ({len(labels)} labelled pairs, {len(configs)} configs)")
    import multiprocessing as mp
    workers = max(mp.cpu_count() - 1, 1)
    with mp.Pool(workers) as pool:
        results = pool.map(_score_one, [(articles, labels, p) for p in configs])

    if out_json:
        with open(out_json, "w") as fh:
            json.dump([{"params": asdict(p), "label": p.label(), **s}
                       for p, s in results], fh, indent=1)
        print(f"(full results for all {len(results)} configs -> {out_json})")

    # Pairwise precision alone is NOT sufficient to rank these, and ranking on
    # it was actively misleading: the labelled pairs are drawn from near
    # neighbours, so a config that collapses hundreds of unrelated articles
    # into one blob can still score well on the sampled pairs. The first grid
    # run's "winner" (overlap/title>=0.4, no SimHash) scored P=0.866 R=0.756
    # while producing a single 646-article cluster. Reject those outright: in
    # three days of Indian news the largest genuine story drew ~20 articles,
    # so anything past MAX_PLAUSIBLE_CLUSTER is a merge failure regardless of
    # what the pair metrics say.
    plausible = [(p, s) for p, s in results
                 if s["largest_cluster"] <= MAX_PLAUSIBLE_CLUSTER]
    rejected = len(results) - len(plausible)

    # Precision-weighted among survivors: a false merge shows the user a wrong
    # framing comparison, a missed merge only leaves a singleton.
    plausible.sort(key=lambda t: (t[1]["precision"] * 2 + t[1]["recall"]), reverse=True)
    print(f"\n{rejected}/{len(results)} configs rejected for producing a cluster "
          f"larger than {MAX_PLAUSIBLE_CLUSTER} articles (blob merges).\n")
    for p, s in plausible[:top]:
        _print_row(p.label(), s)

    if not plausible:
        print("No config passed the blob filter — loosen MAX_PLAUSIBLE_CLUSTER "
              "or tighten the grid.")
        return
    best, best_s = plausible[0]
    print("\nBEST CONFIG")
    print(json.dumps(asdict(best), indent=2))
    print(f"\nvs baseline: precision {base['precision']:.3f} -> {best_s['precision']:.3f}, "
          f"recall {base['recall']:.3f} -> {best_s['recall']:.3f}, "
          f"singleton rate {base['singleton_rate']:.1%} -> {best_s['singleton_rate']:.1%}")


# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="mode", required=True)

    f = sub.add_parser("fetch", help="pull a real article fixture from the DB")
    f.add_argument("--days", type=int, default=3)
    f.add_argument("--out", default="fixture.json")

    pr = sub.add_parser("pairs", help="pick candidate pairs worth labelling")
    pr.add_argument("--fixture", required=True)
    pr.add_argument("--out", default="pairs.json")
    pr.add_argument("--top-k", type=int, default=4)
    pr.add_argument("--window-hours", type=float, default=48.0)
    pr.add_argument("--negatives", type=int, default=100)
    pr.add_argument("--max-pairs", type=int, default=900,
                    help="labelling budget, sampled evenly across the "
                         "similarity range (see _stratify)")

    lb = sub.add_parser("label", help="label pairs via the Haiku Batch API")
    lb.add_argument("--pairs", required=True)
    lb.add_argument("--out", default="labels.json")

    ev = sub.add_parser("eval", help="score one config")
    ev.add_argument("--fixture", required=True)
    ev.add_argument("--labels", required=True)

    gr = sub.add_parser("grid", help="sweep configs and rank them")
    gr.add_argument("--fixture", required=True)
    gr.add_argument("--labels", required=True)
    gr.add_argument("--top", type=int, default=15)
    gr.add_argument("--out-json", help="dump every config's scores for re-ranking")

    ins = sub.add_parser("inspect", help="show a config's merges (no labels/API needed)")
    ins.add_argument("--fixture", required=True)
    ins.add_argument("--baseline", action="store_true",
                     help="inspect PRE-REWORK behaviour (historical, not what runs today)")
    ins.add_argument("--shipped", action="store_true",
                     help="inspect what production actually runs today (SHIPPED_PARAMS)")
    ins.add_argument("--limit", type=int, default=12)
    ins.add_argument("--min-size", type=int, default=2)
    for name, kind in (("metric", str), ("fields", str)):
        ins.add_argument(f"--{name}", type=kind)
    ins.add_argument("--threshold", type=float)
    ins.add_argument("--simhash-max-distance", type=int)
    ins.add_argument("--min-shared", type=int)
    ins.add_argument("--no-simhash", action="store_true")

    args = ap.parse_args()

    if args.mode == "fetch":
        asyncio.run(_fetch(args.days, args.out))
        return

    if args.mode == "pairs":
        articles = load_fixture(args.fixture)
        pairs = _build_pairs(articles, args.top_k, args.window_hours,
                             args.negatives, args.max_pairs)
        slim = [{k: v for k, v in a.items() if not k.startswith("_")} for a in articles]
        with open(args.out, "w") as fh:
            json.dump({"pairs": pairs, "articles": slim}, fh, indent=1)
        print(f"Wrote {len(pairs)} candidate pairs to {args.out}")
        print(f"Estimated labelling cost: ~${len(pairs) * 0.0004:.2f} "
              f"({LABEL_MODEL} via Batch API)")
        return

    if args.mode == "label":
        _do_label(args.pairs, args.out)
        return

    if args.mode == "inspect":
        articles = load_fixture(args.fixture)
        if args.baseline:
            p = CURRENT_PARAMS
        elif args.shipped:
            # The only way to reproduce production here: there is no
            # --same-source-guard flag, and that guard is exactly what stops
            # templated single-outlet feeds fusing into fake mega-clusters.
            p = SHIPPED_PARAMS
        else:
            # The Phase 1 proposal, overridable per flag.
            p = replace(
                CURRENT_PARAMS,
                use_blocking=True, metric="overlap", fields="title",
                threshold=0.5, min_shared=2, window_hours=48.0,
                candidate_limit=None, compare_all_members=True,
            )
            overrides = {
                k: v for k, v in (
                    ("metric", args.metric), ("fields", args.fields),
                    ("threshold", args.threshold),
                    ("simhash_max_distance", args.simhash_max_distance),
                    ("min_shared", args.min_shared),
                ) if v is not None
            }
            if args.no_simhash:
                overrides["use_simhash"] = False
            p = replace(p, **overrides)
        _inspect(articles, p, args.limit, args.min_size)
        return

    articles = load_fixture(args.fixture)
    with open(args.labels) as fh:
        labels = json.load(fh)["labels"]

    if args.mode == "eval":
        print("\nCURRENT PRODUCTION CONFIG")
        _print_row(CURRENT_PARAMS.label(), score(articles, labels, CURRENT_PARAMS))
    else:
        _grid(articles, labels, args.top, args.out_json)


if __name__ == "__main__":
    main()
