#!/usr/bin/env python3
"""
India News App — RSS Feed Verifier Script v2
Includes redirect handling, browser headers, and updated candidate feed URLs.
"""

import json
import os
import sys
import time
import ssl
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from typing import Dict, Any, List

CANDIDATE_FEEDS = [
    # English National
    {"id": "the_hindu", "name": "The Hindu", "category": "national", "url": "https://www.thehindu.com/news/national/feeder/default.rss"},
    {"id": "indian_express", "name": "Indian Express", "category": "national", "url": "https://indianexpress.com/section/india/feed/"},
    {"id": "ndtv_feedburner", "name": "NDTV (Feedburner)", "category": "national", "url": "https://feeds.feedburner.com/ndtvnews-top-stories"},
    {"id": "ndtv_direct", "name": "NDTV (Direct)", "category": "national", "url": "https://www.ndtv.com/rss/top-stories"},
    {"id": "hindustan_times", "name": "Hindustan Times", "category": "national", "url": "https://www.hindustantimes.com/feeds/rss/india-news/rssfeed.xml"},
    {"id": "times_of_india_top", "name": "Times of India (Top)", "category": "national", "url": "https://timesofindia.indiatimes.com/rssfeedstopstories.cms"},
    {"id": "times_of_india_india", "name": "Times of India (India)", "category": "national", "url": "https://timesofindia.indiatimes.com/rssfeeds/-2128936835.cms"},
    {"id": "news18", "name": "News18", "category": "national", "url": "https://www.news18.com/rss/india.xml"},
    {"id": "deccan_herald", "name": "Deccan Herald", "category": "national", "url": "https://www.deccanherald.com/rss/national.rss"},
    {"id": "deccan_herald_alt", "name": "Deccan Herald (Alt)", "category": "national", "url": "https://www.deccanherald.com/rss/india.rss"},
    {"id": "india_today", "name": "India Today", "category": "national", "url": "https://www.indiatoday.in/rss/1206578"},

    # Independent / Analysis
    {"id": "scroll", "name": "Scroll.in", "category": "analysis", "url": "https://scroll.in/feed"},
    {"id": "theprint", "name": "ThePrint", "category": "analysis", "url": "https://theprint.in/feed"},
    {"id": "the_wire", "name": "The Wire", "category": "analysis", "url": "https://thewire.in/rss"},

    # Business / Markets
    {"id": "business_standard", "name": "Business Standard", "category": "business", "url": "https://www.business-standard.com/rss/home_page_top_stories.rss"},
    {"id": "livemint", "name": "Livemint", "category": "business", "url": "https://www.livemint.com/rss/news"},
    {"id": "moneycontrol", "name": "Moneycontrol", "category": "business", "url": "https://www.moneycontrol.com/rss/MCtopnews.xml"},
    {"id": "economic_times", "name": "Economic Times", "category": "business", "url": "https://economictimes.indiatimes.com/rssfeedstopstories.cms"},

    # Official / Authoritative
    {"id": "pib", "name": "PIB Press Releases", "category": "official", "url": "https://pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=3"}
]

# Browser-like headers to bypass simple UA blocks
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/rss+xml;q=0.8,*/*;q=0.7",
    "Accept-Language": "en-US,en;q=0.9",
}

class SmartRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Handle 301, 302, 303, 307, 308 redirects
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req:
            for k, v in HEADERS.items():
                new_req.add_header(k, v)
        return new_req

def verify_feed(feed: Dict[str, str]) -> Dict[str, Any]:
    url = feed["url"]
    result = {
        "id": feed["id"],
        "name": feed["name"],
        "category": feed["category"],
        "url": url,
        "final_url": url,
        "status": "FAILED",
        "http_code": None,
        "latency_ms": None,
        "etag_supported": False,
        "last_modified_supported": False,
        "content_type": None,
        "item_count": 0,
        "sample_headline": None,
        "sample_pub_date": None,
        "error": None
    }

    req = urllib.request.Request(url, headers=HEADERS)
    
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx), SmartRedirectHandler)

    start_time = time.time()
    try:
        with opener.open(req, timeout=10) as response:
            latency = int((time.time() - start_time) * 1000)
            result["latency_ms"] = latency
            result["http_code"] = response.status
            result["final_url"] = response.geturl()
            
            resp_headers = dict(response.info())
            result["etag_supported"] = "etag" in resp_headers or "ETag" in resp_headers
            result["last_modified_supported"] = "last-modified" in resp_headers or "Last-Modified" in resp_headers
            result["content_type"] = resp_headers.get("content-type") or resp_headers.get("Content-Type")

            content = response.read()
            
            try:
                root = ET.fromstring(content)
                items = []
                
                channel = root.find("channel")
                if channel is not None:
                    items = channel.findall("item")
                else:
                    items = root.findall("{http://www.w3.org/2005/Atom}entry") or root.findall("entry")
                
                result["item_count"] = len(items)
                
                if items:
                    first_item = items[0]
                    title_elem = first_item.find("title")
                    pub_elem = first_item.find("pubDate") or first_item.find("{http://www.w3.org/2005/Atom}published") or first_item.find("dc:date")
                    
                    if title_elem is not None and title_elem.text:
                        result["sample_headline"] = title_elem.text.strip()
                    if pub_elem is not None and pub_elem.text:
                        result["sample_pub_date"] = pub_elem.text.strip()

                if result["http_code"] == 200 and result["item_count"] > 0:
                    result["status"] = "PASSED"
                elif result["http_code"] == 200:
                    result["status"] = "WARNING_EMPTY"
                    result["error"] = "HTTP 200 but 0 items parsed from XML"

            except ET.ParseError as e:
                # Snippet of returned content to see if HTML page was served
                snippet = content[:150].decode('utf-8', errors='ignore').replace('\n', ' ')
                result["status"] = "XML_PARSE_ERROR"
                result["error"] = f"Parse error: {str(e)[:60]} | Snippet: {snippet}"
                
    except urllib.error.HTTPError as e:
        result["latency_ms"] = int((time.time() - start_time) * 1000)
        result["http_code"] = e.code
        result["status"] = f"HTTP_{e.code}"
        result["error"] = str(e)
    except Exception as e:
        result["latency_ms"] = int((time.time() - start_time) * 1000)
        result["status"] = "FETCH_ERROR"
        result["error"] = f"{type(e).__name__}: {str(e)}"

    return result

def main():
    print("=================================================================")
    print("      India News App — Tier 1 RSS Feed Verification (v2)          ")
    print("=================================================================\n")

    results = []
    passed_count = 0

    for idx, feed in enumerate(CANDIDATE_FEEDS, 1):
        print(f"[{idx:02d}/{len(CANDIDATE_FEEDS):02d}] Testing {feed['name']}... ", end="", flush=True)
        res = verify_feed(feed)
        results.append(res)
        
        if res["status"] == "PASSED":
            passed_count += 1
            print(f"✅ PASSED | Code: {res['http_code']} | Latency: {res['latency_ms']}ms | Items: {res['item_count']}")
            print(f"     URL: {res['final_url']}")
            print(f"     Headline: \"{res['sample_headline'][:75] if res['sample_headline'] else 'N/A'}\"")
        else:
            print(f"❌ {res['status']} | Code: {res['http_code']} | Latency: {res['latency_ms']}ms")
            print(f"     Error: {res['error']}")
        
        time.sleep(0.3)

    print("\n=================================================================")
    print(f" SUMMARY: {passed_count}/{len(CANDIDATE_FEEDS)} feeds passed validation.")
    print("=================================================================\n")

    out_dir = os.path.dirname(os.path.abspath(__file__))
    report_file = os.path.join(out_dir, "feed_verification_report.json")
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    print(f"Report updated at: {report_file}")

if __name__ == "__main__":
    main()
