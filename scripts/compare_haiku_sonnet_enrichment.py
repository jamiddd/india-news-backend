"""One-off comparison: run the real enrichment prompt (build_enrichment_request)
against N recent clusters in a given source-count range, once with
claude-sonnet-5 (today's actual multi-source model) and once with
claude-haiku-4-5, so we can eyeball whether Haiku's framing-comparison
quality is close enough to justify the cost difference. Read-only — does
not write anything to the DB.

Usage:
    python3 scripts/compare_haiku_sonnet_enrichment.py [--n 3] [--min-sources 2] [--max-sources 5]
"""
import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import httpx
from sqlalchemy import desc, select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import Article, StoryCluster
from app.services.enrichment import build_enrichment_request, extract_text, parse_json_response


async def call_model(request_body: dict, model: str) -> dict:
    body = dict(request_body)
    body["model"] = model
    # claude-haiku-4-5 rejects output_config.effort — only Sonnet gets it.
    if model != "claude-sonnet-5":
        body.pop("output_config", None)
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": settings.ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=body,
        )
        resp.raise_for_status()
        return resp.json()


async def main(n: int, min_sources: int, max_sources: int):
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(StoryCluster)
            .options(selectinload(StoryCluster.articles).selectinload(Article.source))
            .where(
                StoryCluster.distinct_source_count >= min_sources,
                StoryCluster.distinct_source_count <= max_sources,
            )
            .order_by(desc(StoryCluster.id))
            .limit(n)
        )
        clusters = result.scalars().all()

        for cluster in clusters:
            print("=" * 100)
            print(f"Cluster #{cluster.id} — {cluster.headline!r} ({cluster.distinct_source_count} sources)")
            print("=" * 100)

            request_body = build_enrichment_request(cluster, can_compare_framing=True)

            for label, model in [("SONNET (today's actual model)", "claude-sonnet-5"), ("HAIKU", "claude-haiku-4-5")]:
                try:
                    data = await call_model(request_body, model)
                    raw_text = extract_text(data)
                    structured = parse_json_response(raw_text)
                    usage = data.get("usage", {})
                    print(f"\n--- {label} ---")
                    print(f"headline: {structured.get('neutral_headline')}")
                    print("summary_bullets:")
                    for b in structured.get("summary_bullets", []):
                        print(f"  • {b}")
                    print("framing_comparison:")
                    print(json.dumps(structured.get("framing_comparison"), indent=2))
                    print(f"usage: input={usage.get('input_tokens')} output={usage.get('output_tokens')}")
                except Exception as e:
                    print(f"\n--- {label} FAILED: {e} ---")
            print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=3)
    parser.add_argument("--min-sources", type=int, default=2)
    parser.add_argument("--max-sources", type=int, default=10_000)
    args = parser.parse_args()
    asyncio.run(main(args.n, args.min_sources, args.max_sources))
