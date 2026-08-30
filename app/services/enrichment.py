import json
import re
import logging
from typing import Optional, Dict, Any, List
import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from app.models import StoryCluster, Article
from app.services.content_cleaner import decode_entities

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
Your job is to analyze headline and snippet pairs from one or more news outlets
covering the SAME story event. Sometimes only a single outlet is provided —
treat that as a single-source story, not an error.

CRITICAL CONSTRAINTS:
1. Summarize ONLY from the provided headlines and snippets. Do NOT invent outside facts.
2. Generate a neutral, objective, matter-of-fact headline (free of clickbait or bias).
3. Generate a concise summary of AT MOST 3 bullet points. Each bullet must be
   ONE grammatically complete sentence of 35 words or fewer — never merge
   several sentences or facts into a single bullet, and never omit the space
   after a period between clauses.
4. Extract key entities (Persons, Organizations, Locations, Bills/Laws).
5. Of the entities you just extracted, list which ones are merely BACKDROP —
   the setting/context the story happens against — rather than a SUBJECT the
   story is genuinely ABOUT. Backdrop examples: a place name (a story about a
   crime "in Mumbai" isn't about Mumbai), a collective/industry label
   ("Bollywood", "the tech industry"), or a news outlet/publication name that
   got extracted as if it were an entity. Subject examples: a person, a
   specific organization/institution that took an action or was acted upon,
   or a group of people treated as a collective actor (e.g. a political
   party, a protest movement). When in doubt, ask "is the story ABOUT this,
   or does it merely MENTION this in passing" — if the latter, it's backdrop.
   Most stories have zero or one backdrop entities; do not over-flag.
6. If multiple outlets are provided, compare how they framed or angled their
   headlines (descriptive framing comparison). If only one outlet is
   provided, return an empty framing_comparison list — do not invent a
   comparison against outlets that aren't there.

OUTPUT FORMAT: Return strictly valid JSON with keys:
{
  "neutral_headline": "string",
  "summary_bullets": ["string", "string"],
  "entities": {
    "persons": ["string"],
    "organizations": ["string"],
    "locations": ["string"],
    "backdrop": ["string"]
  },
  "topics": ["string"],
  "framing_comparison": [
    {"outlet": "string", "headline_angle": "string"}
  ]
}
"backdrop" must be a subset of the names already listed in persons/
organizations/locations above — do not introduce new names there.
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
        "locations": list(set(found_locs)),
        # No backdrop-vs-subject judgment in the free rule-based path — that's
        # an LLM call away, not a KNOWN_ENTITIES lookup. Left empty rather
        # than guessed, so a missing key/empty list means "the LLM path
        # either didn't run or found nothing," never "confirmed no backdrop."
        "backdrop": []
    }

def generate_framing_comparison(articles: List[Article]) -> List[Dict[str, str]]:
    """Generate descriptive headline angle comparisons across outlets."""
    framing = []
    for art in articles:
        src = art.source.name if art.source else "Source"
        title = art.title
        
        # Categorize angle. Ordered from most to least specific so a headline
        # matching multiple buckets gets the more informative one. The old
        # version fell through to a bare "General Reporting" for anything
        # that didn't hit one of 3 narrow buckets, which in practice was most
        # headlines — these extra buckets and the title-based fallback below
        # cut how often that generic label actually surfaces.
        lower_title = title.lower()
        if any(w in lower_title for w in ["says", "announced", "launches", "approves", "meets", "unveils", "signs", "orders"]):
            angle = "Official / Policy Statement"
        elif any(w in lower_title for w in ["protest", "clash", "row", "lynches", "rape", "crime", " vs ", "slams", "accuses", "blames"]):
            angle = "Conflict & Opposition Impact"
        elif any(w in lower_title for w in ["rate", "sensex", "nifty", "market", "gold", "price", "quarter", "stock", "ipo", "gdp", "inflation"]):
            angle = "Financial & Market Impact"
        elif any(w in lower_title for w in ["court", "hc", "verdict", "judge", "bail", "petition", "cbi", "ed raids", "probe"]):
            angle = "Legal / Investigative"
        elif any(w in lower_title for w in ["dies", "killed", "injured", "accident", "fire", "flood", "cyclone", "quake", "rescue"]):
            angle = "Disaster / Casualty Report"
        elif any(w in lower_title for w in ["wins", "beats", "century", "medal", "tournament", "match", "final"]):
            angle = "Sports Result"
        elif any(w in lower_title for w in ["film", "actor", "actress", "box office", "trailer", "song", "album"]):
            angle = "Entertainment"
        else:
            # Last-resort fallback: use the leading verb/subject phrase
            # (first few words) as a descriptive angle instead of the
            # uninformative generic label, so distinct stories still read
            # as distinct in the framing comparison UI.
            words = title.strip().split()
            angle = " ".join(words[:5]) + ("…" if len(words) > 5 else "")
            if not angle:
                angle = "General Reporting"

        framing.append({
            "outlet": src,
            "headline": title,
            "headline_angle": angle
        })
    return framing

MAX_SUMMARY_BULLETS = 3
MAX_BULLET_CHARS = 280  # ~35 words at average English word length

def _clamp_bullets(bullets: List[str]) -> List[str]:
    """Server-side guardrail behind the prompt's bullet-count/length ask:
    the model (in practice) sometimes ignores "concise 2-3 bullets" for a
    story with a lot of source detail and returns one giant run-on bullet
    instead — this is what actually caps what reaches cluster.summary
    regardless of whether the model complied."""
    clamped = []
    for bullet in bullets[:MAX_SUMMARY_BULLETS]:
        # Missing space after a sentence-ending period is a symptom of the
        # same "sentences got concatenated" failure mode — cheap to repair
        # here rather than ship it to the client as "spreads.Benchmark".
        fixed = re.sub(r'\.(?=[A-Z])', '. ', bullet.strip())
        if len(fixed) > MAX_BULLET_CHARS:
            truncated = fixed[:MAX_BULLET_CHARS].rsplit(" ", 1)[0]
            fixed = truncated.rstrip(".,;:") + "…"
        clamped.append(fixed)
    return clamped

def _sanitize_entities(entities: Dict[str, Any]) -> Dict[str, Any]:
    """Defensively clamps "backdrop" to an actual subset of the
    persons/organizations/locations the model returned in the same response
    — the prompt asks for this but nothing stops a model from naming an
    entity that isn't in those lists (or hallucinating "backdrop" as some
    other shape entirely)."""
    known = set()
    for field_name in ("persons", "organizations", "locations"):
        known.update(entities.get(field_name) or [])
    backdrop = entities.get("backdrop") or []
    if not isinstance(backdrop, list):
        backdrop = []
    entities["backdrop"] = [b for b in backdrop if b in known]
    return entities


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

    # Enrichment is the premium tier's core feature, so every cluster —
    # singletons included — gets the paid AI pass, not just multi-source
    # ones. This used to be gated at len(articles) >= 2 as a cost guardrail
    # (99.7% of clusters were singletons per 2026-08-09 production data), but
    # that made singleton stories — the majority of the feed — permanently
    # stuck on the free rule-based baseline, which isn't good enough for a
    # feature customers pay for. Singletons still get a real AI summary and
    # neutral headline; the prompt is told to return an empty
    # framing_comparison for them instead of inventing a cross-outlet
    # comparison that doesn't exist.
    if settings.ANTHROPIC_API_KEY and len(articles) >= 1:
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

                # decode_entities as a backstop: every other write path to
                # these fields (poller.py's RSS ingestion) decodes entities
                # as its last step, but the model occasionally echoes a
                # literal "&amp;"-style entity from a source title it saw in
                # the prompt, and nothing else here would catch that.
                cluster.headline = decode_entities(structured.get("neutral_headline", cluster.headline))
                bullets = structured.get("summary_bullets", [])
                if bullets:
                    cluster.summary = "\n• " + "\n• ".join(decode_entities(b) for b in _clamp_bullets(bullets))
                if structured.get("entities"):
                    cluster.entities = _sanitize_entities(structured.get("entities"))
                if structured.get("topics"):
                    cluster.topics = structured.get("topics")
                if structured.get("framing_comparison"):
                    cluster.framing_comparison = structured.get("framing_comparison")
                cluster.ai_enriched = True

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
