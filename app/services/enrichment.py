import json
import re
import logging
from typing import Optional, Dict, Any, List
import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from app.models import StoryCluster, Article, utc_now
from app.services.content_cleaner import decode_entities

logger = logging.getLogger(__name__)

# Model routing. Most clusters are single-outlet: a neutral headline plus a
# three-bullet summary from one article is well within Haiku's range, and it
# is the overwhelming majority of the volume, so it sets the cost floor.
#
# Multi-source clusters are the product's premium surface — they carry the
# framing comparison, and after the clustering fix they are roughly 8-10% of
# clusters, so a stronger model on them is affordable. If cost needs cutting
# again, set MULTI_SOURCE_MODEL back to SINGLE_SOURCE_MODEL: it is the only
# line that has to change.
SINGLE_SOURCE_MODEL = "claude-haiku-4-5"
MULTI_SOURCE_MODEL = "claude-sonnet-5"

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
Your job is to analyze headline and article-text pairs from one or more news
outlets covering the SAME story event. Sometimes only a single outlet is
provided — treat that as a single-source story, not an error.

Each article_text is the outlet's own reporting, capped at a fixed length, so
it may end mid-sentence. That is truncation, not the end of the story — never
treat a cut-off clause as a fact about what happened, and never remark on it.

CRITICAL CONSTRAINTS:
1. Summarize ONLY from the provided headlines and article texts. Do NOT invent
   outside facts.
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

def extract_text(data: Dict[str, Any]) -> str:
    """Concatenate the response's text blocks.

    NOT content[0]["text"]. claude-sonnet-5 runs adaptive extended thinking by
    default — omitting the `thinking` parameter does not disable it — so the
    first content block is a `thinking` block, which has no "text" key. The
    old indexing raised KeyError: 'text' on every multi-source enrichment from
    the moment Sonnet routing shipped (0ca7d07, 2026-09-02 22:54). The
    exception was swallowed by the caller's broad `except`, so each call paid
    for Sonnet input, output AND thinking tokens, discarded the whole
    response, left ai_enriched False, and got retried by the timer forever.
    Measured 2026-09-03: 134 clusters stuck in that loop.

    Blocks other than `text` (thinking, tool_use) carry no response text and
    are skipped rather than indexed into.
    """
    return "".join(
        block.get("text", "")
        for block in data.get("content", [])
        if block.get("type") == "text"
    )


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

# There is deliberately no rule-based framing fallback.
#
# A framing comparison is a claim about how outlets *differ*. A keyword
# lookup cannot make that claim: four outlets covering the same earthquake
# all contain "quake", so they all resolve to the identical bucket label,
# and the UI renders four rows saying the same thing under the heading
# "how outlets frame it". The previous version also fell through to
# "first five words of the headline" for anything its verb list missed
# (it matched "announced" but not "announces"), which shipped truncated
# headlines dressed up as analysis. Both were observed in production on
# 2026-09-03.
#
# This is the same fabrication class as the single-outlet bug fixed in
# 0ca7d07: presenting a table lookup as editorial judgment. Framing is now
# populated ONLY by a successful model call. If that call fails,
# framing_comparison stays None and the client hides the section — an
# absent comparison is honest, a fabricated one is not.

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


def distinct_source_count(articles: List[Article]) -> int:
    """Number of distinct outlets in a cluster.

    Deliberately not len(articles): one outlet republishing itself is not a
    cross-outlet framing contrast, and framing eligibility is defined on
    outlets, not articles.
    """
    return len({a.source_id for a in articles if a.source_id is not None})


# The two knobs that bound input cost as multi-source volume grows. Measured
# 2026-09-03 with the count_tokens endpoint over 25 real multi-source
# clusters: snippet-only averaged 1,361 input tokens, full content under
# these caps averaged 2,527 (median 2,069, max 4,519) — so summaries are
# built from ~473 words instead of ~40 at roughly 1.9x the input cost.
#
# MAX_ARTICLES matters more than it looks: without it a single 29-article
# cluster dominates a whole run. Keep both configurable-by-editing here —
# the plan grows multi-source volume deliberately, and cost scales with it.
# See docs/multi-source-feed-plan.md §5.F.
CONTENT_CAP = 1500
MAX_ARTICLES = 6


def select_articles_for_prompt(
    articles: List[Article], max_articles: int = MAX_ARTICLES
) -> List[Article]:
    """Pick at most `max_articles`, one per outlet before any outlet repeats.

    Taking articles in list order would let one prolific outlet fill the
    whole cap — six articles from a single source, which is both a worse
    summary and a framing comparison with nothing to compare. Round-robin by
    outlet instead: every distinct source contributes before any contributes
    twice. This also keeps the cap consistent with can_compare_framing, which
    is computed on the cluster's full article list — a cluster that qualifies
    as multi-source can never be narrowed to one outlet by the cap.

    Within an outlet, longer content first: the article with a real scraped
    body is more use than the one that only has an RSS stub.
    """
    by_source: Dict[Any, List[Article]] = {}
    for art in articles:
        by_source.setdefault(art.source_id, []).append(art)
    for group in by_source.values():
        group.sort(key=lambda a: len(a.content or a.snippet or ""), reverse=True)

    selected: List[Article] = []
    rank = 0
    while len(selected) < max_articles:
        round_took_one = False
        for group in by_source.values():
            if rank < len(group):
                selected.append(group[rank])
                round_took_one = True
                if len(selected) >= max_articles:
                    break
        if not round_took_one:
            break
        rank += 1
    return selected


def apply_baseline_enrichment(cluster: StoryCluster) -> bool:
    """Write the free rule-based enrichment onto the cluster.

    Always runs before the paid call, on every path (sync and batch), so a
    cluster is never left with nothing if the API pass fails. Returns
    can_compare_framing — whether this cluster has enough distinct outlets
    for a framing comparison to be real rather than invented.
    """
    articles = cluster.articles or []
    full_text = f"{cluster.headline} " + " ".join([f"{a.title} {a.snippet or ''}" for a in articles])

    # A framing comparison requires at least two OUTLETS to compare. Measured
    # on production 2026-09-02: 47,018 clusters carried a framing_comparison
    # while only 418 had 2+ distinct sources — i.e. ~99% of the app's
    # flagship feature was an invented comparison of one article against
    # nothing. Everything below keys off this one flag.
    can_compare_framing = distinct_source_count(articles) >= 2

    # Rule-based baseline enrichment
    entities = extract_entities_rule_based(full_text)

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

    # Framing is deliberately NOT cleared here when the cluster can still
    # legitimately have one.
    #
    # This used to unconditionally null it, on the reasoning that a stale
    # framing is worse than none. That reasoning holds for the *content* of a
    # comparison but not for its *availability*, and the cost was measured on
    # production 2026-09-05: because poller.py flips ai_enriched to False on
    # every article a cluster gains, every refinement re-entered this function
    # and blanked the column — and the batch path (enrichment_batch.py) then
    # COMMITS that blank before submitting, so the row served
    # framing_comparison: null for the entire Batch API turnaround (minutes to
    # hours, not the "momentarily" the old comments claimed).
    #
    # That inverted the feature's value. Blanking is triggered by gaining an
    # outlet, so a 33-source national story re-entered the queue constantly and
    # was blank most of the time, while a quiet 2-source story kept its framing
    # forever. The flagship comparison was missing precisely on the stories
    # with the most outlets to compare. A sample of 60 live clusters found 5 of
    # 60 multi-outlet stories serving no framing at any given moment.
    #
    # So: keep the previous comparison in place and let the successful pass
    # overwrite it (see the assignment in enrich_cluster_with_ai, and
    # _apply_batch_results for the batch equivalent). One-outlet-behind beats a
    # blank panel, and the staleness is bounded by the refinement already in
    # flight. Staleness is now handled on the READ path instead — the API hides
    # a comparison whose last_enriched_at is older than FRAMING_MAX_AGE (see
    # app/main.py::_cluster_to_out), which also covers the case the old
    # clearing could not: a batch that fails or never lands at all.
    #
    # The anti-fabrication invariant is unchanged and still enforced here. A
    # cluster that CANNOT have a real comparison is still cleared
    # unconditionally, so a framing can never outlive the multi-outlet
    # condition that justified it (e.g. after scripts/repair_runaway_clusters.py
    # splits a cluster back down to a single source).
    if not can_compare_framing:
        cluster.framing_comparison = None

    return can_compare_framing


def build_enrichment_request(cluster: StoryCluster, can_compare_framing: bool) -> Dict[str, Any]:
    """The Messages API request body for one cluster.

    Shared verbatim by the synchronous path and the Batch API path — a batch
    request's `params` is exactly this object. Keeping one builder is what
    guarantees the two paths can't drift into producing different summaries
    for the same story depending on which queue it happened to go through.
    """
    articles = cluster.articles or []
    # Send the article body we already scrape, not the 250-char RSS stub.
    # 82% of articles carry >400 chars of `content` (avg ~2,985) — the old
    # payload paid an LLM to paraphrase a snippet while the real text sat
    # unused in the same row. Falls back to snippet for the 18% with no
    # scraped body.
    articles_data = [
        {
            "outlet": art.source.name if art.source else "Outlet",
            "headline": art.title,
            "article_text": (art.content or art.snippet or "")[:CONTENT_CAP],
        }
        for art in select_articles_for_prompt(articles)
    ]
    # claude-sonnet-5 thinks adaptively whether or not we ask it to, and
    # those tokens are billed. This is a short structured extraction over a
    # handful of headlines, not a reasoning problem, so cap the depth rather
    # than pay the default "high" effort on every multi-source cluster.
    # Raise this if framing quality measurably suffers — it is the tuning
    # knob, not the model.
    #
    # Deliberately NOT sent on the SINGLE_SOURCE_MODEL path:
    # output_config.effort is rejected by claude-haiku-4-5.
    effort_config = (
        {"output_config": {"effort": "low"}} if can_compare_framing else {}
    )
    return {
        "model": (
            MULTI_SOURCE_MODEL if can_compare_framing else SINGLE_SOURCE_MODEL
        ),
        "max_tokens": 1000,
        # cache_control turns this static system prompt into a cache-eligible
        # block — it's identical on every single enrichment call, so caching
        # it directly cuts input token cost on the repeated portion instead
        # of paying full price to re-send the same ~200 tokens every time.
        # Verify after deploy via the response's
        # usage.cache_read_input_tokens/cache_creation_input_tokens fields
        # actually showing non-zero values, not just assuming this took
        # effect.
        "system": [
            {
                "type": "text",
                "text": ENRICHMENT_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": [
            {
                "role": "user",
                "content": f"Story Articles:\n{json.dumps(articles_data, indent=2)}",
            }
        ],
        **effort_config,
    }


def apply_ai_response(
    cluster: StoryCluster, data: Dict[str, Any], can_compare_framing: bool
) -> None:
    """Write one Messages API response onto the cluster.

    `data` is the raw response body — identical in shape whether it came
    from a synchronous call or from a line of a batch's results file, which
    is why both paths land here. Raises on a malformed response so the
    caller can decide whether to leave the baseline in place (sync) or log
    and continue with the rest of the batch.
    """
    raw_text = extract_text(data)
    if not raw_text:
        raise ValueError(
            f"No text block in response "
            f"(stop_reason={data.get('stop_reason')!r}, "
            f"block types="
            f"{[b.get('type') for b in data.get('content', [])]})"
        )

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
    # THE fabrication bug, and it is subtle. The system prompt
    # explicitly instructs the model to return an EMPTY
    # framing_comparison for a single-outlet story. The model
    # complies and returns []. `if structured.get(...)` is falsy
    # on [], so the assignment was skipped — leaving the
    # rule-based single-outlet baseline written earlier in place.
    # The code punished the model for being correct, which is how
    # 47,018 clusters ended up with a framing comparison when only
    # 418 had two sources to compare.
    #
    # Test presence, not truthiness, so an explicit [] is honoured
    # as the answer it is. And never accept framing for a cluster
    # that cannot have one, whatever the model returns.
    if not can_compare_framing:
        cluster.framing_comparison = None
    elif "framing_comparison" in structured:
        cluster.framing_comparison = structured["framing_comparison"] or None
    cluster.ai_enriched = True
    # Distinct from ai_enriched, which the poller resets to False to request
    # a refresh. last_enriched_at is never reset, so it answers a question
    # ai_enriched cannot: has this cluster EVER had a successful paid pass?
    # That is what separates a story entering the feed (needs a summary now,
    # goes synchronously) from one merely gaining another outlet (a
    # refinement nobody is waiting for, goes to the Batch API at half
    # price). See app/services/enrichment_batch.py.
    cluster.last_enriched_at = utc_now()

    logger.info(f"[Anthropic AI Enriched] Cluster #{cluster.id}")


async def enrich_cluster_with_ai(session: AsyncSession, cluster: StoryCluster) -> Dict[str, Any]:
    """Enrich a story cluster synchronously, falling back to the rule-based
    baseline if the paid call fails.

    This is the path for a cluster entering the feed, where something is
    waiting on the result. Refinement passes go through
    app/services/enrichment_batch.py instead.
    """
    articles = cluster.articles or []
    can_compare_framing = apply_baseline_enrichment(cluster)

    # Every cluster reaching this function gets the paid pass — the
    # multi-source gate lives in the *selection* (scripts/enrich_all_clusters.py
    # and the poller's crossing), not here, so a caller that hands over a
    # cluster deliberately (the admin re-enrich endpoint, a backfill script)
    # still gets what it asked for.
    if settings.ANTHROPIC_API_KEY and len(articles) >= 1:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": settings.ANTHROPIC_API_KEY,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json=build_enrichment_request(cluster, can_compare_framing),
                    timeout=15.0,
                )
                resp.raise_for_status()
                apply_ai_response(cluster, resp.json(), can_compare_framing)
        except Exception as e:
            logger.warning(f"[Anthropic API Skipped/Failed] Using rule-based enrichment: {e}")

    await session.commit()
    return {
        "headline": cluster.headline,
        "entities": cluster.entities,
        "topics": cluster.topics,
        "framing_comparison": cluster.framing_comparison
    }
