"""Fallback "feed" for publishers whose RSS is dead but whose section pages
still publish a schema.org ItemList as JSON-LD.

Moneycontrol's /rss/*.xml feeds froze in 2016-2024; their /news/business/ and
/news/india/ pages are the only live listing, and embed the newest ~25 stories
as `{"@type": "ItemList", "itemListElement": [{"url", "name"}, ...]}`. Entries
come out as feedparser dicts so ingest_source treats them like RSS items; the
page carries no dates, so parse_pub_date falls back to first-seen time.
"""
import json
import re
from typing import List

from feedparser.util import FeedParserDict

_LD_JSON = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I
)


def _iter_nodes(data):
    if isinstance(data, list):
        for d in data:
            yield from _iter_nodes(d)
    elif isinstance(data, dict):
        yield data
        for d in data.get("@graph", []) or []:
            yield from _iter_nodes(d)


def parse_listing_entries(html: str) -> List[FeedParserDict]:
    entries: List[FeedParserDict] = []
    seen = set()
    for m in _LD_JSON.finditer(html or ""):
        try:
            data = json.loads(m.group(1))
        except ValueError:
            continue
        for node in _iter_nodes(data):
            if node.get("@type") != "ItemList":
                continue
            for item in node.get("itemListElement") or []:
                if not isinstance(item, dict):
                    continue
                inner = item.get("item") if isinstance(item.get("item"), dict) else {}
                url = (item.get("url") or inner.get("url") or "").strip()
                name = (item.get("name") or inner.get("name") or "").strip()
                if not url or not name or url in seen:
                    continue
                seen.add(url)
                entries.append(FeedParserDict(title=name, link=url, summary=""))
    return entries
