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

    # Try Anthropic API if key is available
    if settings.ANTHROPIC_API_KEY:
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
                        "system": ENRICHMENT_SYSTEM_PROMPT,
                        "messages": [{"role": "user", "content": f"Story Articles:\n{json.dumps(articles_data, indent=2)}"}]
                    },
                    timeout=15.0
                )
                resp.raise_for_status()
                data = resp.json()
                structured = json.loads(data["content"][0]["text"])

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
