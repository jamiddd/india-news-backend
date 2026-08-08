import hashlib
import re
from urllib.parse import urlparse, parse_qs, urlunparse, urlencode
from typing import Optional, List

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
    text = re.sub(r'[^\w\s]', ' ', text)
    return ' '.join(text.lower().split())

def to_signed_64(val: int) -> int:
    return val - (1 << 64) if val >= (1 << 63) else val

def to_unsigned_64(val: int) -> int:
    return val if val >= 0 else val + (1 << 64)

def compute_simhash(title: str, snippet: Optional[str] = None) -> int:
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
    # Empirically calibrated 2026-08-09 against real production data, not a
    # guess: a real story (PM Modi/JD Vance call) covered by 6 outlets in the
    # same batch had pairwise title+snippet Hamming distances of 16-28 (avg
    # 21.3) despite being the same event, purely because outlets paraphrase
    # headlines/snippets differently — word-level SimHash is sensitive to
    # wording, not just meaning. Meanwhile a same-batch sample of 12 genuinely
    # unrelated articles had distances of 20-39 (avg 30.1), with zero pairs
    # below 20. The old default (4) caught essentially nothing — a check
    # against real production data found 99.7% of clusters were singletons.
    # 18 sits with a 2-point safety margin below the lowest unrelated-pair
    # distance measured (20), catching the closest true-duplicate pairs with
    # zero measured false-positive risk in that sample — but the true-dup and
    # unrelated distributions genuinely overlap in the 20-28 range, so this
    # alone won't merge every cross-outlet duplicate. A more complete fix
    # (shared named-entity overlap, or embeddings) is a known follow-up, not
    # solved by threshold-tuning alone — see india-news-app-handoff.md.
    if hash1 == 0 or hash2 == 0:
        return False
    return hamming_distance(hash1, hash2) <= max_distance
