#!/usr/bin/env python3
import urllib.request
import ssl
import json
import xml.etree.ElementTree as ET

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/rss+xml, application/xml, text/xml, */*"
}

REGIONAL_CANDIDATES = [
    {"name": "NDTV India (Hindi)", "slug": "ndtv-hindi", "url": "https://feeds.feedburner.com/ndtvkhabar", "lang": "hi", "region": "national"},
    {"name": "News18 Hindi", "slug": "news18-hindi", "url": "https://hindi.news18.com/commonfeeds/v1/hin/rss/india.xml", "lang": "hi", "region": "national"},
    {"name": "EastMojo (Northeast)", "slug": "eastmojo-ne", "url": "https://www.eastmojo.com/feed/", "lang": "en", "region": "northeast"},
    {"name": "Assam Tribune (Northeast)", "slug": "assam-tribune", "url": "https://assamtribune.com/feed", "lang": "en", "region": "northeast"},
    {"name": "Northeast Today", "slug": "northeast-today", "url": "https://www.northeasttoday.in/feed/", "lang": "en", "region": "northeast"}
]

print("Testing Regional & Northeast feeds...")
for feed in REGIONAL_CANDIDATES:
    try:
        req = urllib.request.Request(feed["url"], headers=HEADERS)
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            content = resp.read()
            root = ET.fromstring(content)
            channel = root.find("channel")
            items = channel.findall("item") if channel is not None else root.findall("{http://www.w3.org/2005/Atom}entry")
            title = items[0].find("title").text if items and items[0].find("title") is not None else "N/A"
            print(f"✅ PASSED {feed['name']}: {len(items)} items | Region: {feed['region']}")
            print(f"   Sample: \"{title[:75] if title else 'N/A'}\"")
    except Exception as e:
        print(f"❌ FAILED {feed['name']}: {type(e).__name__} - {str(e)[:100]}")
