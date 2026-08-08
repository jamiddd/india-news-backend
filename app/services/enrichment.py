import json
import re
import logging
from typing import Optional, Dict, Any, List
import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from app.models import StoryCluster, Article

logger = logging.getLogger(__name__)

# Common entity patterns in Indian news
KNOWN_ENTITIES = {
    "organizations": [
        "RBI", "SEBI", "Supreme Court", "High Court", "Calcutta HC", "Bombay HC", 
        "Delhi HC", "Parliament", "Rajya Sabha", "Lok Sabha", "PIB", "Meta", 
        "Google", "Apple", "SBI", "Force Motors", "Sensex", "Nifty", "DDU-GKY",
        "Air India", "Ministry of Finance", "Ministry of Home Affairs", "CAQM"
    ],
    "locations": [
        "New Delhi", "Delhi", "Mumbai", "Chennai", "Kolkata", "Bengaluru", 
        "Hyderabad", "Jharkhand", "Karnataka", "Tamil Nadu", "Odisha", 
        "Madhya Pradesh", "Telangana", "NCR", "Dubai", "Iran"
    ]
}

ENRICHMENT_SYSTEM_PROMPT = """
You are an expert news synthesis engine for an Indian news aggregation platform.
Your job is to analyze headline and snippet pairs from multiple news outlets covering the SAME story event.

CRITICAL CONSTRAINTS:
1. Summarize ONLY from the provided headlines and snippets. Do NOT invent outside facts.
2. Generate a neutral, objective, matter-of-fact headline (free of clickbait or bias).
3. Generate a concise 2-3 bullet point summary.
4. Extract key entities (Persons, Organizations, Locations, Bills/Laws).
5. Compare how different news outlets framed or angled their headlines (descriptive framing comparison).

OUTPUT FORMAT: Return strictly valid JSON with keys:
{
  "neutral_headline": "string",
  "summary_bullets": ["string", "string"],
  "entities": {
    "persons": ["string"],
    "organizations": ["string"],
    "locations": ["string"]
  },
  "topics": ["string"],
  "framing_comparison": [
    {"outlet": "string", "headline_angle": "string"}
  ]
}
"""

def parse_json_response(text: str) -> Dict[str, Any]:
    """Parse the model's JSON reply. Despite the prompt asking for strictly
    raw JSON, claude-haiku-4-5 in practice wraps it in ```json ... ``` and
    sometimes adds surrounding commentary the model wasn't asked for. Try, in
    order: raw parse, fence-stripped parse, then a last-resort extraction of
    the first {...} span regardless of what's around it."""
    cleaned = text.strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    fence_match = re.match(r"^```(?:json)?\s*\n?(.*?)\n?```", cleaned, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    brace_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if brace_match:
        return json.loads(brace_match.group(0))

    raise ValueError(f"Could not extract JSON from model response: {cleaned[:200]!r}")

def extract_entities_rule_based(text: str) -> Dict[str, List[str]]:
    """Rule-based entity extraction for Indian news domain."""
    found_orgs = [org for org in KNOWN_ENTITIES["organizations"] if re.search(r'\b' + re.escape(org) + r'\b', text, re.IGNORECASE)]
    found_locs = [loc for loc in KNOWN_ENTITIES["locations"] if re.search(r'\b' + re.escape(loc) + r'\b', text, re.IGNORECASE)]
    return {
        "persons": [],
        "organizations": list(set(found_orgs)),
        "locations": list(set(found_locs))
    }

def generate_framing_comparison(articles: List[Article]) -> List[Dict[str, str]]:
    """Generate descriptive headline angle comparisons across outlets."""
    framing = []
    for art in articles:
        src = art.source.name if art.source else "Source"
        title = art.title
        
        # Categorize angle
        if any(w in title.lower() for w in ["says", "announced", "launches", "approves", "meets"]):
            angle = "Official / Policy Statement"
        elif any(w in title.lower() for w in ["protest", "clash", "row", "lynches", "rape", "crime", "vs"]):
            angle = "Conflict & Opposition Impact"
        elif any(w in title.lower() for w in ["rate", "sensex", "nifty", "market", "gold", "price", "quarter"]):
            angle = "Financial & Market Impact"
        else:
            angle = "General Reporting"

        framing.append({
            "outlet": src,
            "headline": title,
            "headline_angle": angle
        })
    return framing

async def enrich_cluster_with_ai(session: AsyncSession, cluster: StoryCluster) -> Dict[str, Any]:
    """Enrich a story cluster using Anthropic API or fallback rule-based engine."""
    articles = cluster.articles or []
    full_text = f"{cluster.headline} " + " ".join([f"{a.title} {a.snippet or ''}" for a in articles])

    # Rule-based baseline enrichment
    entities = extract_entities_rule_based(full_text)
    framing = generate_framing_comparison(articles)
    
    topics = []
    if any(w in full_text.lower() for w in ["rbi", "market", "sensex", "nifty", "gold", "bank", "coalf", "ethan"]):
        topics.append("Economy & Markets")
    if any(w in full_text.lower() for w in ["court", "hc", "judge", "police", "legal", "bill", "rajya sabha"]):
        topics.append("Judiciary & Law")
    if any(w in full_text.lower() for w in ["cm", "minister", "protest", "mp", "pib", "govt"]):
        topics.append("Governance & Politics")
    if not topics:
        topics.append("National")

    cluster.entities = entities
    cluster.topics = topics
    cluster.framing_comparison = framing

    # Cost guardrail: skip the paid API call entirely for singleton clusters.
    # Framing comparison and cross-outlet neutral-headline synthesis are
    # meaningless with only 1 article, so the rule-based baseline above (free)
    # is just as good as what the model would produce here. Enrichment volume
    # otherwise tracks *article* volume almost 1:1 rather than *distinct
    # story* volume — confirmed against real production data on 2026-08-09,
    # where 99.7% of clusters were singletons — so this directly bounds spend
    # as source count grows, on top of the dedup threshold fix that should
    # reduce how many clusters are singleton in the first place.
    if settings.ANTHROPIC_API_KEY and len(articles) >= 2:
        try:
            articles_data = [
                {
                    "outlet": art.source.name if art.source else "Outlet",
                    "headline": art.title,
                    "snippet": (art.snippet or "")[:250]
                }
                for art in articles
            ]
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": settings.ANTHROPIC_API_KEY,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json"
                    },
                    json={
                        "model": "claude-haiku-4-5",
                        "max_tokens": 1000,
                        # cache_control turns this static system prompt into a
                        # cache-eligible block — it's identical on every single
                        # enrichment call, so caching it directly cuts input
                        # token cost on the repeated portion instead of paying
                        # full price to re-send the same ~200 tokens every
                        # time. Verify after deploy via the response's
                        # usage.cache_read_input_tokens/cache_creation_input_tokens
                        # fields actually showing non-zero values, not just
                        # assuming this took effect.
                        "system": [
                            {
                                "type": "text",
                                "text": ENRICHMENT_SYSTEM_PROMPT,
                                "cache_control": {"type": "ephemeral"}
                            }
                        ],
                        "messages": [{"role": "user", "content": f"Story Articles:\n{json.dumps(articles_data, indent=2)}"}]
                    },
                    timeout=15.0
                )
                resp.raise_for_status()
                data = resp.json()
                content_blocks = data.get("content", [])
                raw_text = content_blocks[0]["text"] if content_blocks else ""

                try:
                    structured = parse_json_response(raw_text)
                except Exception as parse_err:
                    # Log exactly what came back so a parsing failure is
                    # diagnosable from the logs alone, not just "it failed".
                    logger.warning(
                        f"[Anthropic JSON parse failed] Cluster #{cluster.id} "
                        f"stop_reason={data.get('stop_reason')!r} "
                        f"raw_text={raw_text[:500]!r} — {parse_err}"
                    )
                    raise

                cluster.headline = structured.get("neutral_headline", cluster.headline)
                bullets = structured.get("summary_bullets", [])
                if bullets:
                    cluster.summary = "\n• " + "\n• ".join(bullets)
                if structured.get("entities"):
                    cluster.entities = structured.get("entities")
                if structured.get("topics"):
                    cluster.topics = structured.get("topics")
                if structured.get("framing_comparison"):
                    cluster.framing_comparison = structured.get("framing_comparison")

                logger.info(f"[Anthropic AI Enriched] Cluster #{cluster.id}")
        except Exception as e:
            logger.warning(f"[Anthropic API Skipped/Failed] Using rule-based enrichment: {e}")

    await session.commit()
    return {
        "headline": cluster.headline,
        "entities": cluster.entities,
        "topics": cluster.topics,
        "framing_comparison": cluster.framing_comparison
    }
