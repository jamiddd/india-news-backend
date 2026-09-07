"""Prompt-iteration harness for the Timeline/Context tab's LLM narrative —
run this against real data to sanity-check the prompt in app/services/
timeline_narrative.py (the shared module both this script and
scripts/build_story_timelines.py, the production generator, call into).

Usage (needs DATABASE_URL + ANTHROPIC_API_KEY in the environment, same as
any other backend script — run on the server via SSH, or locally against
prod if you have DATABASE_URL set):

    python scripts/test_timeline_narrative.py                  # picks the
                                                                 # 3 longest
                                                                 # chains
    python scripts/test_timeline_narrative.py --chain-of 12345  # the chain
                                                                 # containing
                                                                 # cluster 12345
    python scripts/test_timeline_narrative.py --list             # just print
                                                                 # candidate
                                                                 # chains, no
                                                                 # LLM call
    python scripts/test_timeline_narrative.py --debug-institutions
                                                                 # print which
                                                                 # connecting
                                                                 # entity keys
                                                                 # got caught/
                                                                 # missed by
                                                                 # the
                                                                 # institution
                                                                 # filter

Reuses story_chains.py's load_clusters/load_baseline_rates/build_chains
directly rather than re-deriving chain membership — this script's own code
is just the selection-for-testing + the debug diagnostics.
"""
import argparse
import asyncio
import json
import os
import sys
from typing import Dict, FrozenSet, List, Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.story_chains import (  # noqa: E402
    DAYS,
    GENERIC_METRIC,
    GENERIC_PERCENTILE,
    INSTITUTION_MIN_POSTINGS,
    INSTITUTION_OVERLAP_THRESHOLD,
    USE_INSTITUTION_FILTER,
    Cluster,
    _generic_cutoff,
    build_chains,
    compute_institution_keys,
    load_baseline_rates,
    load_clusters,
)
from app.services.timeline_narrative import TimelineNarrativeError, generate_narrative  # noqa: E402


def debug_institutions(clusters: List[Cluster], baseline_rates: Dict[str, float], chain_ids: FrozenSet[int]) -> None:
    """Replicates build_chains' internal qualifying-keys/institution-filter
    computation (not exported by story_chains.py, which only returns the
    final id->chain assignment) and reports, for one chain, which of its
    connecting entity keys got caught by the institution filter and which
    slipped through — so "this chain reads like a blob" can be checked
    against the actual filter decision instead of guessed at.
    """
    n = len(clusters)
    doc_freq: Dict[str, int] = {}
    for c in clusters:
        for key in c.entity_backdrop:
            doc_freq[key] = doc_freq.get(key, 0) + 1
    in_set_df = {k: v / n for k, v in doc_freq.items()} if n else {}

    if GENERIC_METRIC == "baseline_rate" and baseline_rates:
        metric_values = list(baseline_rates.values())
    else:
        metric_values = list(in_set_df.values())
    cutoff = _generic_cutoff(metric_values, GENERIC_PERCENTILE)

    def is_generic(key: str) -> bool:
        if GENERIC_METRIC == "baseline_rate" and key in baseline_rates:
            return baseline_rates[key] >= cutoff
        return in_set_df.get(key, 0.0) >= cutoff

    qualifying: Dict[int, set] = {}
    for c in clusters:
        keys = set()
        for key, is_backdrop in c.entity_backdrop.items():
            if is_backdrop or is_generic(key):
                continue
            keys.add(key)
        qualifying[c.id] = keys

    institutions: set = set()
    if USE_INSTITUTION_FILTER:
        institutions = compute_institution_keys(
            clusters, qualifying, INSTITUTION_OVERLAP_THRESHOLD, INSTITUTION_MIN_POSTINGS,
        )

    # Global postings (across ALL clusters in the window, not just this
    # chain) — compute_institution_keys' avg_overlap is computed the same
    # way, over every postings of the key window-wide, so reproducing its
    # verdict needs the same input, not just this chain's members.
    postings: Dict[str, List[int]] = {}
    for cid, keys in qualifying.items():
        for key in keys:
            postings.setdefault(key, []).append(cid)

    def avg_overlap(key: str) -> Optional[float]:
        ids = postings.get(key, [])
        if len(ids) < INSTITUTION_MIN_POSTINGS:
            return None
        companions = [qualifying[cid] - {key} for cid in ids]
        overlaps = []
        for a in range(len(companions)):
            for b in range(a + 1, len(companions)):
                ca, cb = companions[a], companions[b]
                if not ca or not cb:
                    overlaps.append(0.0)
                    continue
                overlaps.append(len(ca & cb) / min(len(ca), len(cb)))
        return sum(overlaps) / len(overlaps) if overlaps else 0.0

    # Every qualifying key shared by 2+ members of this specific chain —
    # these are the candidate "why did these get linked" keys.
    key_members: Dict[str, List[int]] = {}
    for cid in chain_ids:
        for key in qualifying.get(cid, set()):
            key_members.setdefault(key, []).append(cid)

    print(f"\n--- institution-filter check ({len(institutions)} institution keys total, threshold={INSTITUTION_OVERLAP_THRESHOLD}, min_postings={INSTITUTION_MIN_POSTINGS}) ---")
    for key, members in sorted(key_members.items(), key=lambda kv: -len(kv[1])):
        if len(members) < 2:
            continue
        verdict = "FILTERED AS INSTITUTION" if key in institutions else "kept as chain-forming key"
        overlap = avg_overlap(key)
        total_postings = len(postings.get(key, []))
        overlap_str = f"avg_overlap={overlap:.3f} over {total_postings} window-wide postings" if overlap is not None else f"only {total_postings} window-wide postings (< min_postings, filter never runs on it)"
        print(f"  {key}: {len(members)}/{total_postings} of its window-wide postings are in this chain -> {verdict} ({overlap_str})")
    print("--- end institution-filter check ---\n")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=DAYS)
    parser.add_argument("--chain-of", type=int, default=None, help="cluster id whose chain to test")
    parser.add_argument("--top", type=int, default=3, help="how many longest chains to test when --chain-of is absent")
    parser.add_argument("--list", action="store_true", help="only print candidate chains, skip the LLM call")
    parser.add_argument("--debug-institutions", action="store_true", help="print which connecting keys were caught/missed by the institution filter")
    args = parser.parse_args()

    from app.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        clusters = await load_clusters(session, args.days)
        baseline_rates = await load_baseline_rates(session)
        by_id = {c.id: c for c in clusters}
        assignment: Dict[int, FrozenSet[int]] = build_chains(clusters, baseline_rates)

        # Distinct chains, longest first — same ranking as the "longest +
        # most recently active" fallback signal discussed for production
        # selection.
        distinct_chains = {frozenset(v) for v in assignment.values() if len(v) > 1}
        ranked = sorted(
            distinct_chains,
            key=lambda ids: (len(ids), max(by_id[i].last_updated_at for i in ids if i in by_id)),
            reverse=True,
        )

        if args.chain_of is not None:
            targets = [assignment.get(args.chain_of, frozenset())]
            targets = [t for t in targets if len(t) > 1]
            if not targets:
                print(f"cluster {args.chain_of} is not part of any chain in the last {args.days}d.")
                return
        else:
            targets = ranked[: args.top]

        print(f"{len(ranked)} distinct chains found over {args.days}d.\n")

        for chain_ids in targets:
            members = sorted((by_id[i] for i in chain_ids if i in by_id), key=lambda c: c.first_seen_at)
            print(f"=== chain of {len(members)}, ids={sorted(chain_ids)} ===")
            for m in members:
                print(f"  [{m.first_seen_at.date()}] ({m.distinct_source_count} outlets) {m.headline}")

            if args.debug_institutions:
                debug_institutions(clusters, baseline_rates, chain_ids)

            if args.list:
                print()
                continue

            print("\n--- calling Claude ---")
            try:
                narrative = await generate_narrative(session, members)
                print(json.dumps(narrative, indent=2))
            except TimelineNarrativeError as e:
                print(f"!!! {e}")
            print()


if __name__ == "__main__":
    asyncio.run(main())
