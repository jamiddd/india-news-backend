import asyncio
import logging
import hashlib
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
import httpx
import feedparser
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import update, desc, func

from app.config import settings
from app.models import Source, Article, StoryCluster, utc_now
from app.services.dedup import compute_url_hash, compute_simhash, is_near_duplicate
from app.services.extractor import extract_full_content
from app.services.image_extractor import extract_rss_image

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
    seen_hashes_this_batch = set()  # some feeds (e.g. Business Today's combined
    # /rssfeeds?id=home) list the same story twice in one fetch — checking only
    # already-committed rows misses that, since neither copy exists in the DB
    # yet; both would pass the check and then collide on insert.
    for entry in items:
        title = getattr(entry, "title", "").strip()
        link = getattr(entry, "link", "").strip()

        # Filter out malformed, empty, or 'undefined' titles
        if not title or not link or title.lower() in ["undefined", "none", "null"] or len(title) < 3:
            continue

        url_hash = compute_url_hash(link)

        if url_hash in seen_hashes_this_batch:
            continue

        # Exact Dedup check
        existing = await session.execute(select(Article).where(Article.url_hash == url_hash))
        if existing.scalar_one_or_none():
            continue

        seen_hashes_this_batch.add(url_hash)

        snippet = getattr(entry, "summary", "") or getattr(entry, "description", "")
        if snippet and snippet.lower() in ["undefined", "none", "null"]:
            snippet = ""

        author = getattr(entry, "author", None)
        pub_date = parse_pub_date(entry)
        rss_image_url = extract_rss_image(entry)

        candidates.append({
            "link": link,
            "title": title,
            "url_hash": url_hash,
            "snippet": snippet,
            "author": author,
            "pub_date": pub_date,
            "rss_image_url": rss_image_url,
        })

    # Pass 2: scrape full article body (+ fallback og:image) per candidate, bounded concurrency.
    semaphore = asyncio.Semaphore(EXTRACTION_CONCURRENCY)

    async def fetch_bounded(link: str, title: str):
        async with semaphore:
            return await extract_full_content(client, link, title)

    extracted = await asyncio.gather(*(fetch_bounded(c["link"], c["title"]) for c in candidates))

    # Pass 3: near-duplicate clustering + insert, now that content is in hand.
    for candidate, extraction in zip(candidates, extracted):
        title = candidate["title"]
        link = candidate["link"]
        url_hash = candidate["url_hash"]
        snippet = candidate["snippet"]
        author = candidate["author"]
        pub_date = candidate["pub_date"]
        content = extraction.content
        # Prefer the RSS feed's own image (usually higher quality / more
        # reliably the lead image) over the scraped page's og:image fallback.
        image_url = candidate["rss_image_url"] or extraction.og_image_url

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
                image_url=image_url,
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
                image_url=image_url,
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

# Arbitrary fixed key identifying "a poll_all_sources cycle is running" as a
# Postgres advisory lock. Cross-process by design: cron invokes the poller
# as a brand-new `docker exec ... python3 scripts/run_poller_now.py` process
# every 15 minutes, sharing no memory with the FastAPI app or any prior
# invocation, so an in-process asyncio.Lock can't see across runs — only a
# lock the DB itself arbitrates can. Session-scoped: Postgres releases it
# automatically if the holding connection drops (crash, timeout), so there's
# no stale-lock cleanup to worry about.
POLL_LOCK_KEY = 872459123

async def poll_all_sources(session: AsyncSession) -> int:
    got_lock = (await session.execute(select(func.pg_try_advisory_lock(POLL_LOCK_KEY)))).scalar()
    if not got_lock:
        logger.warning("Skipping poll: another poll_all_sources cycle is already running.")
        return 0

    try:
        res = await session.execute(select(Source))
        sources = res.scalars().all()
        total_new = 0

        async with httpx.AsyncClient() as client:
            for source in sources:
                source_name = source.name  # capture before the try: once the
                # session's transaction is aborted below, even this lazy ORM
                # attribute access would itself raise PendingRollbackError
                try:
                    count = await ingest_source(session, client, source)
                    total_new += count
                except Exception as e:
                    # A failed commit (e.g. a duplicate-key race, though this
                    # should be rare now that overlapping cycles can't run
                    # concurrently) leaves the shared session's transaction
                    # aborted; without rolling back here, every subsequent
                    # source in this loop would fail too since they all share
                    # this one session. Roll back first, then log — this
                    # source's batch is skipped for this cycle, but it isn't
                    # lost: the next poll re-fetches the feed and dedupes
                    # cleanly against whatever's already committed.
                    await session.rollback()
                    logger.error(f"Ingestion failed for source [{source_name}], skipping this cycle: {e}")

        logger.info(f"[Ingestion Complete] Ingested {total_new} new articles across {len(sources)} sources.")
        return total_new
    finally:
        # Defensive: if something above raised outside the per-source
        # try/except, the session's transaction could still be aborted here,
        # and an aborted session would reject even the unlock query itself.
        await session.rollback()
        await session.execute(select(func.pg_advisory_unlock(POLL_LOCK_KEY)))
