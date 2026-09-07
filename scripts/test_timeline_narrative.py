"""Prompt-iteration harness for the Timeline/Context tab's LLM narrative —
run this against real data BEFORE any table/cron plumbing gets built, so the
output shape and tone are settled first. Not the production generator;
scripts/build_story_timelines.py (unwritten yet) will absorb whatever this
script converges on.

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

Reuses story_chains.py's load_clusters/load_baseline_rates/build_chains
directly rather than re-deriving chain membership — this script's only new
code is the selection-for-testing + prompt + narrative call.
"""
import argparse
import asyncio
import json
import os
import sys
from typing import Dict, FrozenSet, List

import httpx

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.config import settings  # noqa: E402
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

MODEL = "claude-sonnet-5"
API_URL = "https://api.anthropic.com/v1/messages"

# Narrative writing needs real prose judgment (structure a saga into beats,
# decide what's connective-tissue vs. a genuine new development) — the
# cheap/fast Haiku used for the daily-games JSON extraction in llm_gen.py
# isn't the right default here the way it is there.

SYSTEM_PROMPT = """You are writing a "story so far" recap for a news app, in \
the voice of a YouTuber who covers one ongoing story across many videos and \
occasionally posts a big recap once enough has happened. The reader has NOT \
been following along — assume zero prior context, but do not pad with filler.

You will be given a chronological list of news events (each one a distinct \
story-cluster: a date, a headline, and outlet-sourced bullet summaries) that \
all belong to the same unfolding story. Turn them into ONE stitched \
narrative:

- A short "context" paragraph (2-4 sentences) that orients a reader with no \
  prior knowledge: who/what this is about and why it matters.
- A chronological list of "beats" — one entry per genuinely new development. \
  Merge events that are really the same beat reported differently; do not \
  pad the timeline to match the input count. Each beat: a plain-language date \
  reference, a one-line label, and 2-4 sentences of narration in your own \
  words (not copied summary bullets) that connects to what came before ("this \
  followed X", "in response to Y") rather than reading as an isolated blurb.
- No speculation about what happens next. No editorializing/opinion — narrate \
  what happened and why it's connected, not what should happen.
- If the events don't actually form one coherent trail (e.g. they only share \
  a name, not a throughline), say so plainly in "context" and return an empty \
  beats list rather than forcing a narrative.

Respond with ONLY a JSON object, no markdown fences, matching exactly:
{
  "coherent": true | false,
  "title": "short headline for the whole trail",
  "context": "orienting paragraph",
  "beats": [
    {"date_label": "e.g. 'Early August' or 'Sept 3'", "label": "short beat title", "narration": "2-4 sentences", "cluster_ids": [123, 124]}
  ]
}"""


async def call_claude(user_content: str) -> dict:
    if not settings.ANTHROPIC_API_KEY:
        raise SystemExit("ANTHROPIC_API_KEY not set in the environment.")
    async with httpx.AsyncClient(timeout=90) as client:
        resp = await client.post(
            API_URL,
            headers={
                "x-api-key": settings.ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": MODEL,
                "max_tokens": 8000,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": user_content}],
            },
        )
        resp.raise_for_status()
        data = resp.json()
        text = "".join(
            block.get("text", "") for block in data.get("content", []) if block.get("type") == "text"
        )
        cleaned = text.strip().strip("`").removeprefix("json").strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            # A long chain (many beats) can still outrun max_tokens even at
            # 8000 — surface the raw text and the stop_reason instead of a
            # bare traceback, so a truncation is legible as "ran out of
            # tokens" rather than "the JSON is malformed".
            print(f"\n!!! JSON parse failed ({e}); stop_reason={data.get('stop_reason')}")
            print("--- raw response ---")
            print(text)
            print("--- end raw response ---\n")
            raise


def format_chain_for_prompt(members: List[Cluster], summaries: Dict[int, str]) -> str:
    lines = []
    for c in members:
        lines.append(f"--- cluster_id={c.id} | {c.first_seen_at.date().isoformat()} ---")
        lines.append(f"Headline: {c.headline}")
        summary = summaries.get(c.id)
        if summary:
            lines.append(f"Summary:\n{summary}")
        lines.append(f"Outlets covering: {c.distinct_source_count}")
    return "\n".join(lines)


async def fetch_summaries(session, cluster_ids: List[int]) -> Dict[int, str]:
    from sqlalchemy import text

    if not cluster_ids:
        return {}
    result = await session.execute(
        text("SELECT id, summary FROM story_clusters WHERE id = ANY(:ids)"),
        {"ids": cluster_ids},
    )
    return {row.id: row.summary for row in result if row.summary}


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

    # Every qualifying key shared by 2+ members of this specific chain —
    # these are the candidate "why did these get linked" keys.
    key_members: Dict[str, List[int]] = {}
    for cid in chain_ids:
        for key in qualifying.get(cid, set()):
            key_members.setdefault(key, []).append(cid)

    print(f"\n--- institution-filter check ({len(institutions)} institution keys total) ---")
    for key, members in sorted(key_members.items(), key=lambda kv: -len(kv[1])):
        if len(members) < 2:
            continue
        verdict = "FILTERED AS INSTITUTION" if key in institutions else "kept as chain-forming key"
        print(f"  {key}: {len(members)} members, {sorted(members)} -> {verdict}")
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

            summaries = await fetch_summaries(session, [m.id for m in members])
            prompt = format_chain_for_prompt(members, summaries)
            print("\n--- calling Claude ---")
            narrative = await call_claude(prompt)
            print(json.dumps(narrative, indent=2))
            print()


if __name__ == "__main__":
    asyncio.run(main())
