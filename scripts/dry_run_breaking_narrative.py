"""Dry-run harness for the "Breaking" slot's LLM developing-vs-echo pass —
calls the real prompt (app/services/breaking_narrative.py) against a real
cluster's articles and prints the result. Makes one paid Anthropic call;
writes NOTHING to breaking_stories or anywhere else, and does not go
through app.services.breaking's velocity gate — this is for iterating on
and sanity-checking the prompt itself, independent of whether a cluster
would actually qualify for promotion right now.

Use this before trusting the prompt against real production data, and
again any time SYSTEM_PROMPT changes — same purpose
scripts/test_timeline_narrative.py served for the Timeline/Context tab's
prompt.

Usage (on a host with DATABASE_URL + ANTHROPIC_API_KEY set — i.e. via
`docker exec` on the server):

    docker exec news_backend_prod python3 scripts/dry_run_breaking_narrative.py --cluster 46759
    docker exec news_backend_prod python3 scripts/dry_run_breaking_narrative.py --cluster 46759 --since-hours 6
        # simulates a refresh pass: only articles from the last 6h are sent
        # as "new", with all earlier articles' beats first generated as the
        # "existing" baseline — see --refresh below for what that actually
        # exercises.
    docker exec news_backend_prod python3 scripts/dry_run_breaking_narrative.py --cluster 62039 --raw
        # also prints the unverified model output, so a dropped/hallucinated
        # citation is visible rather than silently absent.
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import AsyncSessionLocal  # noqa: E402
from app.models import utc_now  # noqa: E402
from app.services.breaking import _fetch_articles_for_prompt  # noqa: E402
from app.services.breaking_narrative import (  # noqa: E402
    BreakingNarrativeError,
    format_articles_for_prompt,
    judge_and_extract_beats,
    verify_beats,
    call_claude,
    SYSTEM_PROMPT,
)


def _print_beats(beats: list) -> None:
    if not beats:
        print("  (none)")
        return
    for b in beats:
        print(f"  [{b.get('time_label', '?')}] {b.get('label', '?')}")
        print(f"    {b.get('narration', '')}")
        print(f"    cites article_ids: {b.get('article_ids', [])}")


async def main(cluster_id: int, since_hours: int, show_raw: bool) -> None:
    async with AsyncSessionLocal() as session:
        articles = await _fetch_articles_for_prompt(session, cluster_id)
        if not articles:
            print(f"cluster {cluster_id}: no articles found")
            return

        print(f"--- cluster {cluster_id}: {len(articles)} article(s) ---")

        if since_hours is None:
            print("\nRunning a FIRST-PASS judgement over all articles...\n")
            try:
                result = await judge_and_extract_beats(articles)
            except BreakingNarrativeError as e:
                print(f"FAILED: {e}")
                return

            print(f"developing: {result.get('developing')}")
            print(f"title: {result.get('title')}")
            print(f"beats ({len(result.get('beats', []))}):")
            _print_beats(result.get("beats", []))

            if show_raw:
                print("\n--- raw model output before citation verification ---")
                raw = await call_claude(SYSTEM_PROMPT, format_articles_for_prompt(articles))
                print(json.dumps(raw, indent=2))
        else:
            # Simulates a refresh: articles older than the cutoff are
            # treated as the "existing" baseline (first-pass judged), and
            # articles newer than the cutoff are fed as the incremental
            # update — exercising the same append-only path
            # app.services.breaking.process_breaking_cycle uses.
            cutoff = utc_now() - timedelta(hours=since_hours)
            existing_articles = [a for a in articles if a["published_at"] <= cutoff]
            new_articles = [a for a in articles if a["published_at"] > cutoff]

            if not existing_articles:
                print(f"No articles older than {since_hours}h — nothing to seed a baseline with.")
                return
            if not new_articles:
                print(f"No articles newer than {since_hours}h — nothing to refresh with.")
                return

            print(f"\nSeeding baseline from {len(existing_articles)} article(s) "
                  f"older than {since_hours}h...\n")
            try:
                baseline = await judge_and_extract_beats(existing_articles)
            except BreakingNarrativeError as e:
                print(f"FAILED (baseline pass): {e}")
                return
            print(f"baseline developing: {baseline.get('developing')}, "
                  f"{len(baseline.get('beats', []))} beat(s):")
            _print_beats(baseline.get("beats", []))

            print(f"\nRefreshing with {len(new_articles)} article(s) from the last {since_hours}h...\n")
            try:
                refreshed = await judge_and_extract_beats(new_articles, existing_beats=baseline.get("beats", []))
            except BreakingNarrativeError as e:
                print(f"FAILED (refresh pass): {e}")
                return
            print(f"refresh developing: {refreshed.get('developing')}, "
                  f"+{len(refreshed.get('beats', []))} new beat(s):")
            _print_beats(refreshed.get("beats", []))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cluster", type=int, required=True, help="cluster_id to test")
    parser.add_argument("--since-hours", type=int, default=None,
                         help="simulate a refresh pass: articles older than this are the baseline, newer ones are the incremental update")
    parser.add_argument("--raw", action="store_true", dest="raw", help="also print unverified model output (first-pass only)")
    args = parser.parse_args()
    asyncio.run(main(args.cluster, args.since_hours, args.raw))
