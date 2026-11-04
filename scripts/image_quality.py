#!/usr/bin/env python3
"""Prototype: grade the images of one story as low / medium / high resolution.

    python3 scripts/image_quality.py 306196
    python3 scripts/image_quality.py 306196 --base http://127.0.0.1:8000

Why: a story's carousel leads with whichever image came first, and a publisher thumbnail
(e.g. a Guardian image delivered at width=140) stretched over a 420-760px slot looks blurry.
Each story carries 2-3 pictures; the goal is that the lead one is the sharpest.

How: collect every image URL on the story's articles, read each file's real pixel size from its
header with a ranged request (JPEG, PNG, WebP, GIF; a few KB, not the whole file), fall back to
size hints in the URL (width=140, width-1280,height-720, 1600x900, resize=1920,1440) when a host
refuses, then grade against what the site actually displays and rank best-first.

Standard library only, so it can be dropped into the poller later (probe once at ingest, store
width/height on the article, and let every client pick the best image).
"""
import argparse
import json
import re
import struct
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

UA = "Mozilla/5.0 (compatible; OpenIndianNewsImageProbe/0.1)"
HEAD_BYTES = 128 * 1024          # JPEGs with big EXIF/ICC blocks can push the size marker this far in

# What the site displays: story hero up to 760 CSS px wide on desktop, ~390 on a phone at 3x density.
# So a picture is "high" when it can fill the widest slot sharply, "low" when it would visibly blur.
HIGH_W, HIGH_H = 1000, 560       # fills a 760px hero (and a 16:9 crop of it) at ~1.3x
MEDIUM_W = 600                   # fine for a card or the feed hero on a normal screen, soft on the big hero


# ---- reading the size out of a file's first bytes ---------------------------------------
def jpeg_size(b):
    i = 2
    while i + 9 < len(b):
        if b[i] != 0xFF:
            i += 1
            continue
        marker = b[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seg = struct.unpack(">H", b[i + 2:i + 4])[0]
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            h, w = struct.unpack(">HH", b[i + 5:i + 9])
            return w, h
        i += 2 + seg
    return None


def webp_size(b):
    kind = b[12:16]
    if kind == b"VP8 ":
        w, h = struct.unpack("<HH", b[26:30])
        return w & 0x3FFF, h & 0x3FFF
    if kind == b"VP8L":
        bits = struct.unpack("<I", b[21:25])[0]
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if kind == b"VP8X":
        return int.from_bytes(b[24:27], "little") + 1, int.from_bytes(b[27:30], "little") + 1
    return None


def image_size(b):
    if b[:3] == b"\xff\xd8\xff":
        return jpeg_size(b)
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", b[16:24])
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return webp_size(b)
    if b[:6] in (b"GIF87a", b"GIF89a"):
        return struct.unpack("<HH", b[6:10])
    return None


# ---- hints in the URL (used only when the file can't be read) ---------------------------
def url_hint(url):
    u = urllib.parse.unquote(url)
    m = re.search(r"width-(\d+),height-(\d+)", u)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"(?<![\d])(\d{3,4})x(\d{3,4})(?![\d])", u)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"resize=(\d+),(\d+)", u)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"[?&](?:w|width)=(\d+)", u)
    if m:
        return int(m.group(1)), None
    return None


def probe(url):
    """-> dict(url, width, height, bytes, how). Never raises."""
    out = {"url": url, "width": None, "height": None, "bytes": None, "how": None, "error": None}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Range": f"bytes=0-{HEAD_BYTES - 1}", "Accept": "image/*,*/*"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = r.read(HEAD_BYTES)
            total = r.headers.get("Content-Range", "").rpartition("/")[2]
            out["bytes"] = int(total) if total.isdigit() else (int(r.headers["Content-Length"]) if r.headers.get("Content-Length", "").isdigit() else None)
        size = image_size(data)
        if size:
            out["width"], out["height"], out["how"] = size[0], size[1], "file header"
    except Exception as e:  # noqa: BLE001 - a dead host must not stop the others
        out["error"] = f"{type(e).__name__}: {e}"[:80]
    if out["width"] is None:
        hint = url_hint(url)
        if hint:
            out["width"], out["height"], out["how"] = hint[0], hint[1], "url hint"
    return out


def grade(width, height):
    if not width:
        return "unknown"
    if width >= HIGH_W and (height is None or height >= HIGH_H):
        return "high"
    if width >= MEDIUM_W:
        return "medium"
    return "low"


def score(p):
    """Higher is better: pixel area, with a small bonus for a landscape crop (the site shows 16:9 / 3:2)."""
    if not p["width"]:
        return -1
    h = p["height"] or p["width"] * 0.56
    landscape = 1.0 if p["width"] >= h else 0.7
    return p["width"] * h * landscape


# ---- a story's images -------------------------------------------------------------------
def story_images(cluster):
    seen, found = set(), []
    for a in cluster.get("articles", []):
        urls = list(a.get("image_urls") or [])
        if a.get("image_url") and a["image_url"] not in urls:
            urls.insert(0, a["image_url"])
        for u in urls:
            if u and u not in seen:
                seen.add(u)
                found.append({"url": u, "source": a.get("source_name"), "has_video": bool(a.get("video_url") or a.get("has_pending_video"))})
    if cluster.get("image_url") and cluster["image_url"] not in seen:
        found.insert(0, {"url": cluster["image_url"], "source": "(cluster)", "has_video": False})
    return found


def analyse(cluster):
    imgs = story_images(cluster)
    with ThreadPoolExecutor(max_workers=8) as ex:
        probes = list(ex.map(lambda i: probe(i["url"]), imgs))
    rows = []
    for i, p in zip(imgs, probes):
        rows.append({**i, **p, "grade": grade(p["width"], p["height"]), "score": score(p)})
    ranked = sorted(rows, key=lambda r: r["score"], reverse=True)
    return {"id": cluster.get("id"), "headline": cluster.get("headline"), "images": rows, "ranked": ranked,
            "has_high": any(r["grade"] == "high" for r in rows), "best": ranked[0] if ranked and ranked[0]["score"] > 0 else None}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("cluster_id", type=int)
    ap.add_argument("--base", default="https://openindiannews.com")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    req = urllib.request.Request(f"{a.base}/api/v1/clusters/{a.cluster_id}", headers={"User-Agent": UA, "Accept": "application/json"})
    cluster = json.load(urllib.request.urlopen(req, timeout=30))
    res = analyse(cluster)
    if a.json:
        print(json.dumps(res, indent=1, default=str))
        return
    print(f"\nstory {res['id']}: {res['headline']}\n{len(res['images'])} images\n")
    print(f"{'#':>2}  {'grade':<8} {'size':>11}  {'KB':>6}  {'how':<11} source / url")
    order = {r["url"]: n for n, r in enumerate(res["ranked"], 1)}
    for r in res["images"]:
        dim = f"{r['width']}x{r['height'] or '?'}" if r["width"] else "?"
        kb = f"{r['bytes'] // 1024}" if r["bytes"] else "?"
        print(f"{order[r['url']]:>2}  {r['grade']:<8} {dim:>11}  {kb:>6}  {(r['how'] or r['error'] or '-'):<11} {r['source']}: {r['url'][:84]}")
    print()
    if res["best"]:
        b = res["best"]
        print(f"lead with: #1 {b['grade']} {b['width']}x{b['height']} from {b['source']}")
    print("at least one high-res image:", "YES" if res["has_high"] else "NO")


if __name__ == "__main__":
    sys.exit(main())
