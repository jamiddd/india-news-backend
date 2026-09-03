import hashlib
import re
import unicodedata
from urllib.parse import urlparse, parse_qs, urlunparse, urlencode
from typing import Optional, List, Set

# Common English function words, plus generic journalism filler ("report",
# "watch", "exclusive", ...), stripped before hashing/overlap checks. Without
# this, two completely unrelated headlines that happen to share several
# stopwords (which dominate token count in short text) can land deceptively
# close together in SimHash's Hamming space — this was the actual mechanism
# behind a production cluster ("Yash breaks silence on 'Toxic'...") silently
# absorbing 20+ unrelated stories (herpes facts, a Japan typhoon, UPSC
# toppers, a stablecoin launch) over several weeks: the shared-stopword floor
# alone was enough to bring their distances into range.
STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "if", "then", "so", "as", "of",
    "in", "on", "at", "to", "for", "with", "from", "by", "about", "into",
    "over", "after", "before", "during", "amid", "against", "between",
    "is", "are", "was", "were", "be", "been", "being", "has", "have", "had",
    "do", "does", "did", "will", "would", "can", "could", "should", "may",
    "might", "must", "shall", "not", "no", "yes", "it", "its", "this",
    "that", "these", "those", "he", "she", "they", "we", "you", "his",
    "her", "their", "our", "your", "who", "what", "when", "where", "why",
    "how", "which", "than", "too", "also", "just", "more", "most", "some",
    "all", "each", "every", "other", "such", "own", "same", "there", "here",
    "up", "down", "out", "off", "again", "further", "once", "says", "said",
    "say", "report", "reports", "reported", "reportedly", "news", "video",
    "watch", "live", "update", "updates", "latest", "breaking", "exclusive",
})


# Confirmation-gate thresholds, chosen by grid search against 1,020
# LLM-labelled article pairs over a real 3-day / 14,087-article fixture
# (scripts/eval_clustering.py). Measured on that set:
#
#   pre-rework (Jaccard >= 0.25, title+snippet):  precision 1.000, recall 0.027
#   first rework (Jaccard >= 0.40, title, >=2):   precision 0.955, recall 0.542
#   THIS RULE  (Jaccard >= 0.30, title, >=3):     precision 0.901, recall 0.701
#
# The pre-rework gate was not "conservative", it was inert — it found 2.7% of
# genuine same-story pairs, which is what drove the 98.5% singleton rate.
#
# Loosened again 2026-09-03 because recall became the binding constraint on
# the product, not just on this metric: the multi-source feed was running at
# 254 corroborated clusters/day against the 350 it was designed around
# (docs/multi-source-feed-plan.md §9). An error analysis of the 215 missed
# pairs found 85% sitting just under the 0.40 cutoff at a median 0.267, and
# ZERO with no lexical overlap at all — so the recall was reachable by moving
# this constant, and did not need embeddings.
#
# min_shared moves 2 -> 3 alongside the threshold, not independently: at 0.30
# a two-token overlap is too easy, and the grid's ranking has the pair moving
# together.
#
# THE COST, stated plainly: precision 0.955 -> 0.901, i.e. roughly one merge
# in ten is wrong instead of one in twenty-two. Wrong merges feed the framing
# comparison, so a bad cluster presents outlets as framing one story
# differently when they are covering different events. The observed residue
# is "topic blobs" of genuinely related stories (several states' draft
# electoral rolls; a Tamil Nadu headline that names two unrelated stories at
# once), not the templated single-outlet mega-merges the same-source guard
# exists to stop — those stayed absent under inspect.
#
# Re-tune by re-running that script, not by intuition. Mirror any change here
# into SHIPPED_PARAMS in scripts/eval_clustering.py, or the harness measures
# an algorithm nobody is running.
MIN_TITLE_JACCARD = 0.30
MIN_SHARED_TOKENS = 3


def significant_tokens(title: str, snippet: Optional[str] = None) -> Set[str]:
    """Content-bearing tokens (stopwords and short words dropped).

    `snippet` is accepted for callers that still want the combined bag, but
    topic confirmation deliberately no longer uses it — see title_tokens().
    """
    combined = normalize_text(f"{title} {snippet or ''}")
    return {
        tok for tok in combined.split()
        if len(tok) >= 4 and tok not in STOPWORDS
    }


def title_tokens(title: str) -> Set[str]:
    """Content-bearing tokens from the headline alone.

    Snippets are excluded on purpose. Folding them in was the single biggest
    cause of missed merges: snippet length varies wildly between outlets (a
    wire brief vs. a full writeup), so the Jaccard *union* denominator
    explodes while the intersection stays roughly title-sized, and two outlets
    covering the same event land around J=0.05-0.15 — far below any usable
    threshold. Titles are short and comparable in length, so overlap between
    them is a much better-behaved signal. Snippets are still stored and shown;
    they just don't decide identity.
    """
    return {
        tok for tok in normalize_text(title).split()
        if len(tok) >= 4 and tok not in STOPWORDS
    }


def shares_topic(
    title1: str, title2: str,
    min_shared: int = MIN_SHARED_TOKENS,
    min_jaccard: float = MIN_TITLE_JACCARD,
) -> bool:
    """Confirmation gate: do these two headlines describe the same story?

    Requires both a minimum count of shared significant tokens and a minimum
    Jaccard overlap over those tokens, so two short headlines can't pass by
    coincidentally sharing a couple of common (but non-stopword) words like
    "india" or "minister".
    """
    tokens1 = title_tokens(title1)
    tokens2 = title_tokens(title2)
    if not tokens1 or not tokens2:
        return False
    shared = tokens1 & tokens2
    if len(shared) < min_shared:
        return False
    jaccard = len(shared) / len(tokens1 | tokens2)
    return jaccard >= min_jaccard

TRACKING_PARAMS = {
    'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content',
    'rss', 'ref', 'cmpid', 'gad_source', 'gclid', 'fbclid', 'at_custom1'
}

def canonicalize_url(url: str) -> str:
    parsed = urlparse(url.strip())
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip('/')
    query_dict = parse_qs(parsed.query)
    filtered_query = {
        k: v for k, v in query_dict.items() 
        if k.lower() not in TRACKING_PARAMS
    }
    query_str = urlencode(filtered_query, doseq=True)
    return urlunparse((scheme, netloc, path, parsed.params, query_str, ''))

def compute_url_hash(url: str) -> str:
    canonical = canonicalize_url(url)
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()

def normalize_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'<[^>]+>', ' ', text)
    # Not `re.sub(r'[^\w\s]', ' ', text)`: Python's \w excludes combining
    # marks (Unicode category Mn/Mc) — the vowel signs and virama that
    # Devanagari (and other Indic scripts) syllables are built from. That
    # blunt regex shattered every Hindi word at each vowel sign, leaving
    # significant_tokens() with almost nothing but stray digits to compare
    # — two IDENTICAL Hindi PIB press-release titles were being flagged as
    # sharing no topic. Keep anything unicodedata classifies as a letter,
    # mark, or number; drop only actual punctuation/symbols.
    text = ''.join(
        ch if ch.isspace() or unicodedata.category(ch)[0] in ('L', 'M', 'N') else ' '
        for ch in text
    )
    return ' '.join(text.lower().split())

def to_signed_64(val: int) -> int:
    return val - (1 << 64) if val >= (1 << 63) else val

def to_unsigned_64(val: int) -> int:
    return val if val >= 0 else val + (1 << 64)

def compute_simhash(title: str, snippet: Optional[str] = None) -> int:
    # Deliberately NOT stopword-filtered, unlike significant_tokens() below.
    # This value is only ever used as a coarse, index-friendly prefilter
    # (is_near_duplicate) — dropping stopwords here makes already-short
    # headlines even shorter, which makes the resulting hash *more* volatile
    # to a single differing word, not less (confirmed empirically: it broke
    # detection of "RBI cuts repo rate by 25 basis points" vs "...25 bps" as
    # near-duplicates). Precision against false positives comes from
    # shares_topic()'s content-word overlap check at the call site, not from
    # tightening this function.
    combined = normalize_text(f"{title} {snippet or ''}")
    tokens = combined.split()
    if not tokens:
        return 0

    v = [0] * 64
    for token in tokens:
        h = int(hashlib.md5(token.encode('utf-8')).hexdigest()[:16], 16)
        for i in range(64):
            bitmask = 1 << i
            if h & bitmask:
                v[i] += 1
            else:
                v[i] -= 1

    fingerprint = 0
    for i in range(64):
        if v[i] >= 0:
            fingerprint |= (1 << i)

    return to_signed_64(fingerprint)

def hamming_distance(hash1: int, hash2: int) -> int:
    h1_u = to_unsigned_64(hash1)
    h2_u = to_unsigned_64(hash2)
    x = (h1_u ^ h2_u) & 0xFFFFFFFFFFFFFFFF
    return bin(x).count('1')

def is_near_duplicate(hash1: int, hash2: int, max_distance: int = 18) -> bool:
    """NO LONGER USED FOR CLUSTERING. Retained for the simhash column, the
    repair scripts, and its tests.

    The grid search that produced MIN_TITLE_JACCARD also swept this gate, and
    no configuration retaining it at any threshold beat dropping it outright:
    `simhash off` and `simhash <= 30` scored identically at the top, and
    nothing at the old <= 18 survived at all. That matches the note below —
    a real 6-outlet story averaged 21.3, *above* the threshold — meaning this
    check was rejecting true matches before the confirmation gate ever saw
    them. It is a veto that only ever subtracted recall, so poller.py no
    longer calls it.
    """
    # Empirically calibrated 2026-08-09 against real production data: a real
    # story (PM Modi/JD Vance call) covered by 6 outlets in the same batch
    # had pairwise title+snippet Hamming distances of 16-28 (avg 21.3)
    # despite being the same event, purely because outlets paraphrase
    # headlines/snippets differently — word-level SimHash is sensitive to
    # wording, not just meaning. A same-batch sample of 12 genuinely
    # unrelated articles had distances of 20-39 (avg 30.1). The old default
    # (4) caught essentially nothing (99.7% of clusters were singletons).
    #
    # That measured overlap between the two distributions (20-28) means this
    # check alone is NOT sufficient to call two articles duplicates — it is
    # a cheap, index-friendly *prefilter* only. Real production data showed
    # its false-positive risk is not "small", it's severe: a cluster left
    # unchecked at this threshold silently absorbed 20+ completely unrelated
    # stories over several weeks, because short headlines share enough
    # common (stopword) bit-pattern "floor" to coincidentally land in range.
    # Every call site MUST additionally confirm with shares_topic() (actual
    # content-word overlap) before treating two articles as the same story —
    # see the matching loop in poller.py.
    if hash1 == 0 or hash2 == 0:
        return False
    return hamming_distance(hash1, hash2) <= max_distance
