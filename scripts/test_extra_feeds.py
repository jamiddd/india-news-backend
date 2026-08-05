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

EXTRA_TESTS = [
    # Fixed redirect URLs
    {"name": "News18 (Canonical)", "url": "https://www.news18.com/rss/india.xml"},
    {"name": "Scroll.in (Trailing slash)", "url": "https://scroll.in/feed/"},
    {"name": "Deccan Herald (Top Stories)", "url": "https://www.deccanherald.com/rss/top-stories.rss"},
    {"name": "Deccan Herald (India)", "url": "https://www.deccanherald.com/rss/india.rss"},
    {"name": "Financial Express", "url": "https://www.financialexpress.com/feed/"},
    {"name": "Business Today", "url": "https://www.businesstoday.in/rss/topstories"},
    {"name": "The Tribune (India)", "url": "https://www.tribuneindia.com/rss/nation"},
    {"name": "Telegraph India", "url": "https://www.telegraphindia.com/rss/india"},
    {"name": "NDTV India (Hindi)", "url": "https://feeds.feedburner.com/ndtvkhabar"},
    {"name": "News18 Hindi", "url": "https://hindi.news18.com/rss/khabar.xml"}
]

class FollowAllRedirects(urllib.request.HTTPRedirectHandler):
    def http_error_308(self, req, fp, code, msg, headers):
        new_url = headers.get('Location')
        if new_url:
            new_req = urllib.request.Request(new_url, headers=HEADERS)
            return self.parent.open(new_req)
        return super().http_error_308(req, fp, code, msg, headers)
    def http_error_307(self, req, fp, code, msg, headers):
        return self.http_error_308(req, fp, code, msg, headers)

opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx), FollowAllRedirects)

print("Testing expanded candidates...")
for test in EXTRA_TESTS:
    try:
        req = urllib.request.Request(test["url"], headers=HEADERS)
        with opener.open(req, timeout=8) as resp:
            content = resp.read()
            final_url = resp.geturl()
            root = ET.fromstring(content)
            channel = root.find("channel")
            items = channel.findall("item") if channel is not None else root.findall("entry")
            title = items[0].find("title").text if items and items[0].find("title") is not None else "N/A"
            print(f"✅ {test['name']}: {len(items)} items | Final URL: {final_url}")
            print(f"   Sample: \"{title[:70] if title else 'N/A'}\"")
    except Exception as e:
        print(f"❌ {test['name']}: {type(e).__name__} - {str(e)[:100]}")
