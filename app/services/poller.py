import asyncio
import logging
from datetime import datetime, timezone
import time
from typing import List, Optional, Dict, Any
import httpx
import feedparser
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import update, desc

from backend.app.config import settings
from backend.app.models import Source, Article, StoryCluster, utc_now
from backend.app.services.dedup import compute_url_hash, compute_simhash, is_near_duplicate

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": settings.INGESTION_USER_AGENT,
    "Accept": "application/rss+xml, application/xml, text/xml, application/atom+xml, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

async def fetch_source_feed(client: httpx.AsyncClient, source: Source) -> Dict[str, Any]:
    headers = dict(HEADERS)
    if source.etag:
        headers["If-None-Match"] = source.etag
    if source.last_modified:
        headers["If-Modified-Since"] = source.last_modified

    try:
        response = await client.get(source.feed_url, headers=headers, timeout=6.0, follow_redirects=True)
        
        if response.status_code == 304:
            return {"source_id": source.id, "status": "not_modified", "code": 304, "items": []}
        
        response.raise_for_status()
        
        new_etag = response.headers.get("ETag") or response.headers.get("etag")
        new_last_modified = response.headers.get("Last-Modified") or response.headers.get("last-modified")

        parsed = feedparser.parse(response.content)
        items = parsed.entries or []

        return {
            "source_id": source.id,
            "status": "success",
            "code": response.status_code,
            "etag": new_etag,
            "last_modified": new_last_modified,
            "items": items
        }

    except Exception as e:
        logger.error(f"[Ingestion Error] Source '{source.name}' feed fetch error: {e}")
        return {"source_id": source.id, "status": "error", "error": str(e), "items": []}


def parse_pub_date(entry: Any) -> datetime:
    if hasattr(entry, 'published_parsed') and entry.published_parsed:
        return datetime.fromtimestamp(time.mktime(entry.published_parsed), tz=timezone.utc)
    elif hasattr(entry, 'updated_parsed') and entry.updated_parsed:
        return datetime.fromtimestamp(time.mktime(entry.updated_parsed), tz=timezone.utc)
    return utc_now()


async def ingest_source(session: AsyncSession, client: httpx.AsyncClient, source: Source) -> int:
    res = await fetch_source_feed(client, source)
    
    source.last_fetched_at = utc_now()
    
    if res["status"] == "error":
        source.consecutive_failures += 1
        if source.consecutive_failures >= 5:
            source.status = "degraded"
        await session.commit()
        return 0

    source.consecutive_failures = 0
    if source.status == "degraded":
        source.status = "active"

    if res["status"] == "not_modified":
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

    for entry in items:
        title = getattr(entry, "title", "").strip()
        link = getattr(entry, "link", "").strip()
        
        # Filter out malformed, empty, or 'undefined' titles
        if not title or not link or title.lower() in ["undefined", "none", "null"] or len(title) < 3:
            continue

        snippet = getattr(entry, "summary", "") or getattr(entry, "description", "")
        if snippet and snippet.lower() in ["undefined", "none", "null"]:
            snippet = ""
            
        author = getattr(entry, "author", None)
        pub_date = parse_pub_date(entry)
        
        simhash_val = compute_simhash(title, snippet)

        # Pass 2: Near-Duplicate Clustering
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

    await session.commit()
    return new_articles_count


async def poll_all_sources(session: AsyncSession) -> int:
    result = await session.execute(select(Source).where(Source.status != "disabled"))
    sources = result.scalars().all()
    
    if not sources:
        return 0

    total_new = 0
    limits = httpx.Limits(max_keepalive_connections=20, max_connections=30)
    async with httpx.AsyncClient(verify=False, limits=limits) as client:
        for source in sources:
            count = await ingest_source(session, client, source)
            total_new += count

    logger.info(f"[Ingestion Complete] Ingested {total_new} new articles across {len(sources)} sources.")
    return total_new
