"""
Entity canonicalization for the graph-based feed ranking redesign (piece 1:
global importance). See the "Feed ranking redesign" design doc/memory for
the full picture — this module only does one job: turn the free-text
entity names LLM-extracted per cluster (app.services.enrichment's
`entities` JSON) into a stable key so mentions of the same real-world
entity aggregate together in app.services.poller's entity_stats recompute,
instead of fragmenting across spelling variants.

Deliberately crude (string normalization + a small hand-maintained alias
table), not NLP entity-linking — per the design notes, a real graph/linker
is a "graduate later if needed" step, not a v1 requirement. Anything not in
ALIASES canonicalizes to its own normalized string, so it still counts
mentions correctly, it just won't merge with any other spelling of the same
entity.
"""
import re
from typing import Dict, Literal, Optional

EntityType = Literal["person", "organization", "location"]

# Honorifics/titles stripped before matching, so "Dr. S. Jaishankar" and
# "S. Jaishankar" canonicalize to the same key. Word-boundary matched,
# case-insensitive — see _HONORIFIC_PATTERN below.
_HONORIFICS = [
    "dr", "shri", "smt", "hon'ble", "honble", "mr", "mrs", "ms",
    "justice", "cji",
]
_HONORIFIC_PATTERN = re.compile(
    r"\b(?:" + "|".join(re.escape(h) for h in _HONORIFICS) + r")\.?\b",
    re.IGNORECASE,
)
_PUNCT_PATTERN = re.compile(r"[^\w\s]")
_WHITESPACE_PATTERN = re.compile(r"\s+")

# Known variant -> canonical key, seeded from enrichment.py's KNOWN_ENTITIES
# canonical spellings. Extend this over time as fragmentation shows up in
# real entity_stats data — not meant to be exhaustive on day one.
_ALIASES: Dict[str, str] = {
    # Organizations
    "rbi": "reserve_bank_of_india",
    "reserve bank of india": "reserve_bank_of_india",
    "the central bank": "reserve_bank_of_india",
    "sebi": "securities_and_exchange_board_of_india",
    "securities and exchange board of india": "securities_and_exchange_board_of_india",
    "supreme court": "supreme_court",
    "supreme court of india": "supreme_court",
    "sc": "supreme_court",
    "cji": "chief_justice_of_india",
    "chief justice of india": "chief_justice_of_india",
    "parliament": "parliament",
    "parliament of india": "parliament",
    "rajya sabha": "rajya_sabha",
    "lok sabha": "lok_sabha",
    "pib": "press_information_bureau",
    "press information bureau": "press_information_bureau",
    "sbi": "state_bank_of_india",
    "state bank of india": "state_bank_of_india",
    "sensex": "sensex",
    "bse sensex": "sensex",
    "nifty": "nifty",
    "nifty 50": "nifty",
    "air india": "air_india",
    "ministry of finance": "ministry_of_finance",
    "finance ministry": "ministry_of_finance",
    "ministry of home affairs": "ministry_of_home_affairs",
    "home ministry": "ministry_of_home_affairs",
    "mha": "ministry_of_home_affairs",
    "election commission": "election_commission_of_india",
    "election commission of india": "election_commission_of_india",
    "eci": "election_commission_of_india",
    # Locations
    "delhi": "delhi",
    "new delhi": "delhi",
    "ncr": "delhi",
    "bengaluru": "bengaluru",
    "bangalore": "bengaluru",
    "mumbai": "mumbai",
    "bombay": "mumbai",
    "kolkata": "kolkata",
    "calcutta": "kolkata",
    "chennai": "chennai",
    "madras": "chennai",
}


def _normalize(name: str) -> str:
    """Lowercase, strip honorifics/punctuation, collapse whitespace."""
    normalized = name.strip().lower()
    normalized = _HONORIFIC_PATTERN.sub(" ", normalized)
    normalized = _PUNCT_PATTERN.sub(" ", normalized)
    normalized = _WHITESPACE_PATTERN.sub(" ", normalized).strip()
    return normalized


def canonicalize_entity(name: str, entity_type: EntityType) -> Optional[str]:
    """
    Turn a free-text entity name into a stable key for entity_stats, scoped
    by type so e.g. a person and a location that happen to normalize to the
    same string never collide.

    Returns None for empty/whitespace-only input (LLM output occasionally
    includes blank strings in an entities list) — callers should skip these.
    """
    normalized = _normalize(name)
    if not normalized:
        return None
    canonical = _ALIASES.get(normalized, normalized.replace(" ", "_"))
    return f"{entity_type}:{canonical}"
