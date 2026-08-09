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


def significant_tokens(title: str, snippet: Optional[str] = None) -> Set[str]:
    """Content-bearing tokens (stopwords and short words dropped) used to
    confirm two articles actually share subject matter, on top of — not
    instead of — the SimHash distance check."""
    combined = normalize_text(f"{title} {snippet or ''}")
    return {
        tok for tok in combined.split()
        if len(tok) >= 4 and tok not in STOPWORDS
    }


def shares_topic(
    title1: str, snippet1: Optional[str],
    title2: str, snippet2: Optional[str],
    min_shared: int = 2, min_jaccard: float = 0.25,
) -> bool:
    """Whole-content-word confirmation gate: requires both a minimum count
    of shared significant tokens and a minimum Jaccard overlap, so two short
    headlines can't pass just by coincidentally sharing a couple of common
    (but non-stopword) words like "india" or "minister"."""
    tokens1 = significant_tokens(title1, snippet1)
    tokens2 = significant_tokens(title2, snippet2)
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
