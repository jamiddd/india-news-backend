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

def is_near_duplicate(hash1: int, hash2: int, max_distance: int = 4) -> bool:
    if hash1 == 0 or hash2 == 0:
        return False
    return hamming_distance(hash1, hash2) <= max_distance
