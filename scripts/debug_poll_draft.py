"""Diagnostic twin of generate_draft() in app/services/polls.py — runs the
exact same candidate-selection + Claude call, but prints the raw response
and shows *why* validate_draft rejected it, instead of just raising. Writes
nothing to the database. Use this when generate_poll_now.py fails to produce
a draft and you need to see what Claude actually returned.

Usage:
    python3 scripts/debug_poll_draft.py
"""
import asyncio
import json
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import httpx
from sqlalchemy import desc, select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import Article, StoryCluster, utc_now
from app.services.enrichment import parse_json_response
from app.services.polls import validate_draft


async def main():
    if not settings.ANTHROPIC_API_KEY:
        print("ANTHROPIC_API_KEY is not configured")
        return

    async with AsyncSessionLocal() as session:
        cutoff = utc_now() - timedelta(hours=24)
        candidates = (await session.execute(
            select(StoryCluster).options(selectinload(StoryCluster.articles).selectinload(Article.source))
            .where(StoryCluster.first_seen_at >= cutoff, StoryCluster.distinct_source_count >= 2)
            .order_by(desc(StoryCluster.headline_score)).limit(20)
        )).scalars().all()
        shortlist = list(candidates[:8])
        print(f"{len(candidates)} candidate clusters in last 24h, sending the top {len(shortlist)} to Claude")
        for cluster in shortlist:
            print(f"  [{cluster.id}] {cluster.headline}")
        if not shortlist:
            print("No corroborated stories available — nothing to send to Claude")
            return

        payload = [{
            "cluster_id": cluster.id,
            "headline": cluster.headline,
            "summary": cluster.summary,
            "articles": [{"outlet": a.source.name if a.source else "Source", "headline": a.title, "snippet": (a.snippet or "")[:220]} for a in cluster.articles[:5]],
        } for cluster in shortlist]
        system = """You draft a neutral daily public-opinion poll for an Indian news app. Use only supplied reporting. Choose one suitable policy, civic, economic, science, technology, education, environment or public-service issue. Never poll on a person's guilt, tragedy, death, communal identity, religion, caste, active crime, or unverifiable claim. Return only JSON: {\"source_cluster_id\": integer, \"question\": string ending ?, \"context\": one neutral factual sentence, \"options\": 2-4 mutually exclusive balanced strings}. Hard limits: question 20-180 characters ending in \"?\", context 20-300 characters, every option non-empty and at most 80 characters — write options as short noun phrases, not sentences. Avoid loaded premises and include nuance when a binary choice is misleading."""

        async with httpx.AsyncClient(timeout=25) as client:
            response = await client.post("https://api.anthropic.com/v1/messages", headers={
                "x-api-key": settings.ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"
            }, json={"model": "claude-haiku-4-5", "max_tokens": 700, "system": system, "messages": [{"role": "user", "content": json.dumps(payload)}]})
            response.raise_for_status()
        raw = response.json().get("content") or []
        raw_text = raw[0]["text"] if raw else ""
        print("\n--- Raw Claude response text ---")
        print(raw_text)

        data = parse_json_response(raw_text)
        print("\n--- Parsed JSON ---")
        print(json.dumps(data, indent=2))

        try:
            question, context, options = validate_draft(data)
            print("\nvalidate_draft PASSED")
            print(f"Question: {question}")
            print(f"Context: {context}")
            print(f"Options: {options}")
        except ValueError as exc:
            print(f"\nvalidate_draft FAILED: {exc}")
            question = str(data.get("question") or "")
            context = str(data.get("context") or "")
            options = [str(v).strip() for v in data.get("options") or []]
            print(f"  question length={len(question)} ends_with_?={question.endswith('?')}")
            print(f"  context length={len(context)}")
            print(f"  option count={len(options)} distinct={len({v.casefold() for v in options})}")
            print(f"  option lengths={[len(v) for v in options]}")


if __name__ == "__main__":
    asyncio.run(main())
