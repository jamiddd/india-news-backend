#!/usr/bin/env python3
"""
Standalone End-to-End Pipeline Verification Script
Runs full ingestion, 2-pass deduplication, and story clustering against 13 verified live RSS feeds using standard Python 3.
"""

import hashlib
import json
import re
import sqlite3
import ssl
import time
import urllib.request
import xml.etree.ElementTree as ET
from urllib.parse import urlparse, parse_qs, urlunparse, urlencode
from datetime import datetime, timezone

VERIFIED_FEEDS = [
    {"name": "The Hindu", "slug": "the-hindu", "url": "https://www.thehindu.com/news/national/feeder/default.rss"},
    {"name": "Indian Express", "slug": "indian-express", "url": "https://indianexpress.com/section/india/feed/"},
    {"name": "NDTV", "slug": "ndtv", "url": "https://feeds.feedburner.com/ndtvnews-top-stories"},
    {"name": "Hindustan Times", "slug": "hindustan-times", "url": "https://www.hindustantimes.com/feeds/rss/india-news/rssfeed.xml"},
    {"name": "Times of India (Top)", "slug": "toi-top", "url": "https://timesofindia.indiatimes.com/rssfeedstopstories.cms"},
    {"name": "Times of India (India)", "slug": "toi-india", "url": "https://timesofindia.indiatimes.com/rssfeeds/-2128936835.cms"},
    {"name": "News18", "slug": "news18", "url": "https://www.news18.com/commonfeeds/v1/eng/rss/india.xml"},
    {"name": "India Today", "slug": "india-today", "url": "https://www.indiatoday.in/rss/1206578"},
    {"name": "Livemint", "slug": "livemint", "url": "https://www.livemint.com/rss/news"},
    {"name": "Moneycontrol", "slug": "moneycontrol", "url": "https://www.moneycontrol.com/rss/MCtopnews.xml"},
    {"name": "Economic Times", "slug": "economic-times", "url": "https://economictimes.indiatimes.com/rssfeedstopstories.cms"},
    {"name": "Business Today", "slug": "business-today", "url": "https://www.businesstoday.in/rssfeeds?id=home"},
    {"name": "PIB Press Releases", "slug": "pib", "url": "https://www.pib.gov.in/RssMain.aspx?ModId=6&Lang=2&Regid=3&reg=48"}
]

TRACKING_PARAMS = {'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content', 'rss', 'ref', 'cmpid', 'gad_source'}

def canonicalize_url(url: str) -> str:
    parsed = urlparse(url.strip())
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip('/')
    query_dict = parse_qs(parsed.query)
    filtered_query = {k: v for k, v in query_dict.items() if k.lower() not in TRACKING_PARAMS}
    return urlunparse((scheme, netloc, path, parsed.params, urlencode(filtered_query, doseq=True), ''))

def compute_url_hash(url: str) -> str:
    return hashlib.sha256(canonicalize_url(url).encode('utf-8')).hexdigest()

def normalize_text(text: str) -> str:
    if not text: return ""
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'[^\w\s]', ' ', text)
    return ' '.join(text.lower().split())

def compute_simhash(title: str, snippet: str = "") -> int:
    combined = normalize_text(f"{title} {snippet or ''}")
    tokens = combined.split()
    if not tokens: return 0
    v = [0] * 64
    for token in tokens:
        h = int(hashlib.md5(token.encode('utf-8')).hexdigest()[:16], 16)
        for i in range(64):
            v[i] += 1 if (h & (1 << i)) else -1
    fingerprint = 0
    for i in range(64):
        if v[i] >= 0: fingerprint |= (1 << i)
    return fingerprint

def to_signed_64(val: int) -> int:
    return val - (1 << 64) if val >= (1 << 63) else val

def hamming_distance(h1: int, h2: int) -> int:
    return bin((h1 ^ h2) & 0xFFFFFFFFFFFFFFFF).count('1')

def setup_sqlite_db():
    conn = sqlite3.connect(":memory:")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT, slug TEXT UNIQUE, feed_url TEXT
        )
    """)
    c.execute("""
        CREATE TABLE story_clusters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            headline TEXT, summary TEXT, article_count INTEGER DEFAULT 1,
            first_seen_at TEXT, last_updated_at TEXT
        )
    """)
    c.execute("""
        CREATE TABLE articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER, url TEXT, url_hash TEXT UNIQUE,
            title TEXT, snippet TEXT, simhash INTEGER, cluster_id INTEGER,
            FOREIGN KEY(source_id) REFERENCES sources(id),
            FOREIGN KEY(cluster_id) REFERENCES story_clusters(id)
        )
    """)
    for s in VERIFIED_FEEDS:
        c.execute("INSERT INTO sources (name, slug, feed_url) VALUES (?, ?, ?)", (s["name"], s["slug"], s["url"]))
    conn.commit()
    return conn

def run_pipeline():
    print("=================================================================")
    print("      India News App — Standalone Ingestion & Clustering Pipeline ")
    print("=================================================================\n")
    
    conn = setup_sqlite_db()
    c = conn.cursor()

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, Gecko) Chrome/122.0.0.0 Safari/537.36"
    }
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    total_fetched = 0
    exact_dups = 0
    near_dups_clustered = 0
    clusters_created = 0

    c.execute("SELECT id, name, feed_url FROM sources")
    sources = c.fetchall()

    for source_id, name, feed_url in sources:
        print(f"Polling '{name}'... ", end="", flush=True)
        try:
            req = urllib.request.Request(feed_url, headers=headers)
            with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
                content = resp.read()
                root = ET.fromstring(content)
                channel = root.find("channel")
                items = channel.findall("item") if channel is not None else root.findall("{http://www.w3.org/2005/Atom}entry")
                
                source_new = 0
                for item in items:
                    t_elem = item.find("title")
                    l_elem = item.find("link")
                    d_elem = item.find("description") or item.find("summary")
                    
                    title = t_elem.text.strip() if t_elem is not None and t_elem.text else ""
                    link = l_elem.text.strip() if l_elem is not None and l_elem.text else ""
                    snippet = d_elem.text.strip() if d_elem is not None and d_elem.text else ""

                    if not title or not link:
                        continue

                    total_fetched += 1
                    url_h = compute_url_hash(link)

                    # Pass 1: Exact Dedup
                    c.execute("SELECT id FROM articles WHERE url_hash = ?", (url_h,))
                    if c.fetchone():
                        exact_dups += 1
                        continue

                    sim_h = compute_simhash(title, snippet)
                    sim_h_signed = to_signed_64(sim_h)

                    # Pass 2: Near-dup Clustering check against recent clusters
                    c.execute("""
                        SELECT c.id, a.simhash 
                        FROM story_clusters c 
                        JOIN articles a ON a.cluster_id = c.id 
                        ORDER BY c.last_updated_at DESC LIMIT 100
                    """)
                    existing_clusters = c.fetchall()

                    matched_cluster_id = None
                    for cid, ex_simhash in existing_clusters:
                        if ex_simhash is not None:
                            # Convert back to unsigned for bitwise hamming calculation
                            ex_simhash_u = ex_simhash if ex_simhash >= 0 else ex_simhash + (1 << 64)
                            if hamming_distance(sim_h, ex_simhash_u) <= 4:
                                matched_cluster_id = cid
                                break

                    now_str = datetime.now(timezone.utc).isoformat()

                    if matched_cluster_id:
                        c.execute("""
                            INSERT INTO articles (source_id, url, url_hash, title, snippet, simhash, cluster_id) 
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                        """, (source_id, link, url_h, title, snippet, sim_h_signed, matched_cluster_id))
                        c.execute("""
                            UPDATE story_clusters 
                            SET article_count = article_count + 1, last_updated_at = ? 
                            WHERE id = ?
                        """, (now_str, matched_cluster_id))
                        near_dups_clustered += 1
                    else:
                        c.execute("""
                            INSERT INTO story_clusters (headline, summary, article_count, first_seen_at, last_updated_at) 
                            VALUES (?, ?, 1, ?, ?)
                        """, (title, snippet, now_str, now_str))
                        cluster_id = c.lastrowid
                        c.execute("""
                            INSERT INTO articles (source_id, url, url_hash, title, snippet, simhash, cluster_id) 
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                        """, (source_id, link, url_h, title, snippet, sim_h_signed, cluster_id))
                        clusters_created += 1

                    source_new += 1

                print(f"✅ Items: {len(items)} | New Ingested: {source_new}")
        except Exception as e:
            print(f"❌ Error: {e}")

    conn.commit()

    print("\n=================================================================")
    print("                    PIPELINE RESULTS SUMMARY                     ")
    print("=================================================================")
    print(f"Total raw RSS items evaluated:    {total_fetched}")
    print(f"Pass 1 Exact Duplicates (Dropped): {exact_dups}")
    print(f"Pass 2 Near-Duplicates Clustered:  {near_dups_clustered}")
    print(f"Story Clusters Formed:            {clusters_created}")
    print("=================================================================\n")

    # Display Top Multi-Source Story Clusters
    c.execute("""
        SELECT id, headline, article_count 
        FROM story_clusters 
        WHERE article_count > 1 
        ORDER BY article_count DESC 
        LIMIT 5
    """)
    multi_clusters = c.fetchall()

    if multi_clusters:
        print("🔥 TOP MULTI-OUTLET STORY CLUSTERS FORMED:")
        print("-----------------------------------------------------------------")
        for cid, headline, count in multi_clusters:
            print(f"\n[Cluster #{cid}] (Covered by {count} articles)")
            print(f"   Headline: \"{headline}\"")
            c.execute("""
                SELECT s.name, a.title 
                FROM articles a 
                JOIN sources s ON a.source_id = s.id 
                WHERE a.cluster_id = ?
            """, (cid,))
            arts = c.fetchall()
            for src_name, a_title in arts:
                print(f"   • [{src_name}]: \"{a_title[:80]}\"")

if __name__ == "__main__":
    run_pipeline()
