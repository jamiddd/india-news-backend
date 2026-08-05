import asyncio
import logging
import hashlib
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
import httpx
import feedparser
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import update, desc

from app.config import settings
from app.models import Source, Article, StoryCluster, utc_now
from app.services.dedup import compute_url_hash, compute_simhash, is_near_duplicate
from app.services.extractor import extract_full_content

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Cap how many articles we scrape for full content at once, per source,
# so one feed's poll can't hammer a publisher's site or stall the poller.
EXTRACTION_CONCURRENCY = 5

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/rss+xml, application/xml, text/xml, */*"
}

def parse_pub_date(entry) -> datetime:
    parsed_tuple = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if parsed_tuple:
        try:
            return datetime(*parsed_tuple[:6], tzinfo=timezone.utc)
        except Exception:
            pass
    return utc_now()

async def fetch_feed_data(client: httpx.AsyncClient, source: Source) -> Dict[str, Any]:
    headers = dict(HEADERS)
    if source.etag:
        headers["If-None-Match"] = source.etag
    if source.last_modified:
        headers["If-Modified-Since"] = source.last_modified

    try:
        response = await client.get(source.feed_url, headers=headers, follow_redirects=True, timeout=12.0)
        
        if response.status_code == 304:
            logger.info(f"Feed [{source.name}] returned 304 Not Modified. Skipping.")
            return {"status": 304}

        if response.status_code != 200:
            logger.warning(f"Feed [{source.name}] returned HTTP status {response.status_code}")
            return {"status": response.status_code}

        etag = response.headers.get("etag")
        last_modified = response.headers.get("last-modified")

        parsed = feedparser.parse(response.text)
        return {
            "status": 200,
            "etag": etag,
            "last_modified": last_modified,
            "items": parsed.entries
        }
    except Exception as e:
        logger.error(f"Error fetching feed [{source.name}]: {str(e)}")
        return {"status": 500, "error": str(e)}

async def ingest_source(session: AsyncSession, client: httpx.AsyncClient, source: Source) -> int:
    res = await fetch_feed_data(client, source)
    if res.get("status") != 200:
        source.last_polled_at = utc_now()
        await session.commit()
        return 0

    if res.get("etag"):
        source.etag = res["etag"]
    if res.get("last_modified"):
        source.last_modified = res["last_modified"]

    new_articles_count = 0
    items = res.get("items", [])

    recent_clusters_query = await session.execute(
        select(StoryCluster).order_by(desc(StoryCluster.last_updated_at)).limit(100)
    )
    recent_clusters = list(recent_clusters_query.scalars().all())

    # Pass 1: filter malformed/duplicate entries down to real candidates.
    candidates = []
    for entry in items:
        title = getattr(entry, "title", "").strip()
        link = getattr(entry, "link", "").strip()

        # Filter out malformed, empty, or 'undefined' titles
        if not title or not link or title.lower() in ["undefined", "none", "null"] or len(title) < 3:
            continue

        url_hash = compute_url_hash(link)

        # Exact Dedup check
        existing = await session.execute(select(Article).where(Article.url_hash == url_hash))
        if existing.scalar_one_or_none():
            continue

        snippet = getattr(entry, "summary", "") or getattr(entry, "description", "")
        if snippet and snippet.lower() in ["undefined", "none", "null"]:
            snippet = ""

        author = getattr(entry, "author", None)
        pub_date = parse_pub_date(entry)

        candidates.append({
            "link": link,
            "title": title,
            "url_hash": url_hash,
            "snippet": snippet,
            "author": author,
            "pub_date": pub_date,
        })

    # Pass 2: scrape full article body for each candidate, bounded concurrency.
    semaphore = asyncio.Semaphore(EXTRACTION_CONCURRENCY)

    async def fetch_bounded(link: str) -> Optional[str]:
        async with semaphore:
            return await extract_full_content(client, link)

    contents = await asyncio.gather(*(fetch_bounded(c["link"]) for c in candidates))

    # Pass 3: near-duplicate clustering + insert, now that content is in hand.
    for candidate, content in zip(candidates, contents):
        title = candidate["title"]
        link = candidate["link"]
        url_hash = candidate["url_hash"]
        snippet = candidate["snippet"]
        author = candidate["author"]
        pub_date = candidate["pub_date"]

        simhash_val = compute_simhash(title, snippet)

        matched_cluster: Optional[StoryCluster] = None
        for cluster in recent_clusters:
            rep_article = await session.get(Article, cluster.representative_article_id) if cluster.representative_article_id else None
            if rep_article and rep_article.simhash and is_near_duplicate(simhash_val, rep_article.simhash, max_distance=4):
                matched_cluster = cluster
                break

        if matched_cluster:
            article = Article(
                source_id=source.id,
                url=link,
                url_hash=url_hash,
                title=title,
                snippet=snippet,
                content=content,
                author=author,
                published_at=pub_date,
                simhash=simhash_val,
                cluster_id=matched_cluster.id
            )
            session.add(article)
            matched_cluster.article_count += 1
            matched_cluster.last_updated_at = utc_now()
        else:
            new_cluster = StoryCluster(
                headline=title,
                summary=snippet,
                article_count=1,
                first_seen_at=pub_date,
                last_updated_at=pub_date
            )
            session.add(new_cluster)
            await session.flush()

            article = Article(
                source_id=source.id,
                url=link,
                url_hash=url_hash,
                title=title,
                snippet=snippet,
                content=content,
                author=author,
                published_at=pub_date,
                simhash=simhash_val,
                cluster_id=new_cluster.id
            )
            session.add(article)
            await session.flush()
            new_cluster.representative_article_id = article.id
            recent_clusters.insert(0, new_cluster)

        new_articles_count += 1

    source.last_polled_at = utc_now()
    await session.commit()
    logger.info(f"Source [{source.name}] ingestion complete: {new_articles_count} new articles.")
    return new_articles_count

async def poll_all_sources(session: AsyncSession) -> int:
    res = await session.execute(select(Source))
    sources = res.scalars().all()
    total_new = 0

    async with httpx.AsyncClient() as client:
        for source in sources:
            count = await ingest_source(session, client, source)
            total_new += count

    logger.info(f"[Ingestion Complete] Ingested {total_new} new articles across {len(sources)} sources.")
    return total_new
