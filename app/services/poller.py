import asyncio
import logging
import hashlib
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from typing import Optional, List, Dict, Any, Set, Tuple
import feedparser
from curl_cffi.requests import AsyncSession as CurlAsyncSession
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import update, desc, func, text

from app.config import settings
from app.models import Source, Article, StoryCluster, EntityStat, utc_now
from app.services.entity_graph import canonicalize_entity
from app.services.decay import ema_update
from app.services.explore_bandit import recompute_explore_promotions
from app.services.dedup import compute_url_hash, compute_simhash, is_near_duplicate, shares_topic
from app.services.extractor import ExtractedArticle, extract_full_content, is_youtube_video_url, IMPERSONATE
from app.services.image_extractor import extract_rss_image, extract_rss_video, is_placeholder_image
from app.services.content_cleaner import decode_entities, clean_extracted_text

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Cap how many articles we scrape for full content at once, per source,
# so one feed's poll can't hammer a publisher's site or stall the poller.
EXTRACTION_CONCURRENCY = 5

# Drop any RSS item whose own <pubDate> is older than this, so a feed that
# infrequently refreshes (confirmed e.g. on News18's crypto feed, whose
# newest item was already ~40 days stale at check time, vs. CoinDesk/
# Cointelegraph updating within hours) can't keep re-surfacing month-old
# "news" just because it's still sitting in the feed's item list. Items with
# no parseable pubDate default to "now" in parse_pub_date() and always pass.
MAX_ARTICLE_AGE = timedelta(days=4)

# Only conditional-GET headers here — no User-Agent/Accept. curl_cffi's
# impersonate= already sends a full, internally-consistent Chrome header set
# (User-Agent, Accept, sec-ch-ua, ...) matched to its TLS fingerprint; adding
# our own User-Agent on top would make the two disagree, which is itself a
# bot-detection signal.
HEADERS = {
    "Accept": "application/rss+xml, application/xml, text/xml, */*"
}

# Source.category values that identify a specific geography rather than a
# topic. Two sources in different geographic categories are never the same
# story for clustering purposes — see the guard in ingest_source's matching
# loop below.
GEOGRAPHIC_CATEGORIES = {"northeast", "regional_south", "regional_west", "regional_east"}

def parse_pub_date(entry) -> datetime:
    parsed_tuple = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if parsed_tuple:
        try:
            return datetime(*parsed_tuple[:6], tzinfo=timezone.utc)
        except Exception:
            pass
    return utc_now()

async def fetch_feed_data(client: CurlAsyncSession, source: Source) -> Dict[str, Any]:
    headers = dict(HEADERS)
    if source.etag:
        headers["If-None-Match"] = source.etag
    if source.last_modified:
        headers["If-Modified-Since"] = source.last_modified

    try:
        response = await client.get(
            source.feed_url, headers=headers, allow_redirects=True, timeout=12.0, impersonate=IMPERSONATE
        )
        
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

async def ingest_source(session: AsyncSession, client: CurlAsyncSession, source: Source) -> int:
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

    # Which sources have already contributed to each of these clusters —
    # used below to keep distinct_source_count a genuine distinct-outlet
    # count (article_count alone isn't source-deduped: two articles from
    # the same outlet would both increment it).
    cluster_source_ids: Dict[int, Set[int]] = defaultdict(set)
    if recent_clusters:
        membership_rows = await session.execute(
            select(Article.cluster_id, Article.source_id).where(
                Article.cluster_id.in_([c.id for c in recent_clusters])
            )
        )
        for cid, sid in membership_rows:
            cluster_source_ids[cid].add(sid)

    # Pass 1: filter malformed/duplicate entries down to real candidates.
    candidates = []
    seen_hashes_this_batch = set()  # some feeds (e.g. Business Today's combined
    # /rssfeeds?id=home) list the same story twice in one fetch — checking only
    # already-committed rows misses that, since neither copy exists in the DB
    # yet; both would pass the check and then collide on insert.
    for entry in items:
        title = decode_entities(getattr(entry, "title", "").strip())
        link = getattr(entry, "link", "").strip()

        # Filter out malformed, empty, or 'undefined' titles
        if not title or not link or title.lower() in ["undefined", "none", "null"] or len(title) < 3:
            continue

        url_hash = compute_url_hash(link)

        if url_hash in seen_hashes_this_batch:
            continue

        # Exact Dedup check — id only, not the full row (matches the title
        # check right below; this one used to pull the whole Article,
        # content included, just to test existence).
        existing = await session.execute(select(Article.id).where(Article.url_hash == url_hash))
        if existing.scalar_one_or_none():
            continue

        # Recurring templated-headline check: some outlets run a standing
        # daily/rolling "roundup" page under the exact same headline every
        # time (confirmed e.g. Cointelegraph's "Here's what happened in
        # crypto today" — verbatim-identical title each day, generic
        # evergreen snippet, and the article page itself has no scrapable
        # body content — it's a live-updating landing page, not a story).
        # A genuine news headline is essentially never repeated word-for-
        # word by the same outlet on a different day, so if this exact
        # title has already been ingested from this source before, treat it
        # as that same template recurring rather than real news.
        title_key = title.strip().lower()
        existing_title = await session.execute(
            select(Article.id).where(
                Article.source_id == source.id,
                func.lower(Article.title) == title_key,
            )
        )
        if existing_title.scalar_one_or_none():
            continue

        seen_hashes_this_batch.add(url_hash)

        snippet = decode_entities(getattr(entry, "summary", "") or getattr(entry, "description", ""))
        # Some publishers' RSS <description> bakes the same sign-off/CTA
        # boilerplate into every single item (e.g. News18's "CNN-News18 is
        # your trusted source..." + social links block, verbatim on every
        # article). clean_extracted_text already strips this for scraped
        # article content — reuse it here too. Left uncleaned, that shared
        # boilerplate text was long and repetitive enough to make
        # shares_topic() (dedup.py) see two completely unrelated articles
        # from the same outlet as sharing a topic, since the boilerplate
        # alone supplied enough matching significant tokens.
        snippet = clean_extracted_text(snippet, title) or snippet
        if snippet and snippet.lower() in ["undefined", "none", "null"]:
            snippet = ""

        author = getattr(entry, "author", None)
        pub_date = parse_pub_date(entry)
        if utc_now() - pub_date > MAX_ARTICLE_AGE:
            continue
        rss_image_url = extract_rss_image(entry)
        rss_video_url = extract_rss_video(entry)

        candidates.append({
            "link": link,
            "title": title,
            "url_hash": url_hash,
            "snippet": snippet,
            "author": author,
            "pub_date": pub_date,
            "rss_image_url": rss_image_url,
            "rss_video_url": rss_video_url,
        })

    # Pass 2: scrape full article body (+ fallback og:image) per candidate,
    # bounded concurrency — unless the source is RSS-only, in which case the
    # scrape is known to fail and is skipped rather than spent (see
    # Source.rss_only). Those candidates keep whatever the feed gave them.
    semaphore = asyncio.Semaphore(EXTRACTION_CONCURRENCY)

    async def fetch_bounded(link: str, title: str):
        async with semaphore:
            return await extract_full_content(client, link, title)

    if source.rss_only:
        extracted = [ExtractedArticle(None, None) for _ in candidates]
    else:
        extracted = await asyncio.gather(*(fetch_bounded(c["link"], c["title"]) for c in candidates))

    # Prefetch every recent cluster's representative article, once for the
    # whole batch, selecting only what near-dup matching below actually
    # reads (simhash/title/snippet + the source's category). This used to be
    # a `select(Article, Source.category)` — full row, including the scraped
    # body — re-run inside the candidate loop for every (candidate, cluster)
    # pair, i.e. re-fetching the same ~100 article bodies once per candidate.
    # With Supabase metering egress, that was the single largest line item
    # in the whole poll cycle for zero benefit — content was never read here.
    rep_article_ids = {c.representative_article_id for c in recent_clusters if c.representative_article_id}
    rep_info: Dict[int, Tuple[SimpleNamespace, Optional[str]]] = {}
    if rep_article_ids:
        rep_rows = await session.execute(
            select(Article.id, Article.simhash, Article.title, Article.snippet, Source.category)
            .join(Source, Article.source_id == Source.id)
            .where(Article.id.in_(rep_article_ids))
        )
        for aid, simhash, rep_title, rep_snippet, category in rep_rows:
            rep_info[aid] = (
                SimpleNamespace(simhash=simhash, title=rep_title, snippet=rep_snippet),
                category,
            )

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
        if await is_placeholder_image(session, source.id, image_url, url_hash):
            image_url = None
        # Prefer a real video (RSS video enclosure/media, then scraped
        # og:video) over the image — a video is a strictly richer lead media
        # when a story has both.
        video_url = candidate["rss_video_url"] or extraction.og_video_url
        # The Shorts flag and duration describe the *scraped* video, so they
        # only apply when that's the one that won.
        if video_url and video_url == extraction.og_video_url:
            video_is_short = extraction.video_is_short
            video_duration_seconds = extraction.video_duration_seconds
        else:
            video_is_short = None
            video_duration_seconds = None
        # A YouTube video is deliberately NOT media_type="video". The app
        # can't play one inline anywhere in its own design (YouTube's chrome
        # doesn't mix with it, and the logo/title/end-screen can't be
        # suppressed), so the card renders as an ordinary image card with a
        # duration badge that opens the dedicated fullscreen screen. Ranking
        # it as a video story would promote it for a richness the feed never
        # actually shows. video_url is still stored — it's what that
        # fullscreen screen plays.
        if video_url and not is_youtube_video_url(video_url):
            media_type = "video"
        else:
            media_type = "image" if image_url else None

        simhash_val = compute_simhash(title, snippet)
        # Computed once here at scrape time so explore_bandit's
        # _estimate_word_count can read this int instead of fetching the
        # whole body later just to count words.
        word_count_val = max(len(content.split()), 1) if content else None

        matched_cluster: Optional[StoryCluster] = None
        for cluster in recent_clusters:
            rep_article = None
            rep_source_category = None
            if cluster.representative_article_id:
                cached = rep_info.get(cluster.representative_article_id)
                if cached:
                    rep_article, rep_source_category = cached

            # max_distance intentionally omitted here — is_near_duplicate's own
            # default (18) is the empirically-calibrated value; keep this call
            # site from silently overriding it if that default is ever revised.
            if not (rep_article and rep_article.simhash and is_near_duplicate(simhash_val, rep_article.simhash)):
                continue

            # is_near_duplicate is only a cheap prefilter (see its docstring)
            # — confirm with actual shared content words before merging.
            # Without this, unrelated short headlines that happen to share
            # enough common filler words coincidentally land within Hamming
            # range of each other; in production this let one cluster
            # silently absorb 20+ completely unrelated stories over weeks.
            if not shares_topic(title, snippet, rep_article.title, rep_article.snippet):
                continue

            # SimHash on short headlines is loose enough (threshold 18/64
            # bits) that unrelated stories can coincidentally land within
            # range. That's tolerable within one geography/topic, but never
            # across two different "geographic" categories (northeast vs.
            # regional_south, etc.) — merging those doesn't just misfile one
            # article, it makes a whole cluster (and everything in it)
            # appear under the wrong region's tab. Refuse that merge and
            # fall through to starting a new cluster instead.
            if (
                source.category in GEOGRAPHIC_CATEGORIES
                and rep_source_category in GEOGRAPHIC_CATEGORIES
                and source.category != rep_source_category
            ):
                continue

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
                word_count=word_count_val,
                image_url=image_url,
                video_url=video_url,
                media_type=media_type,
                video_is_short=video_is_short,
                video_duration_seconds=video_duration_seconds,
                author=author,
                published_at=pub_date,
                simhash=simhash_val,
                cluster_id=matched_cluster.id
            )
            session.add(article)
            matched_cluster.article_count += 1
            matched_cluster.last_updated_at = utc_now()
            # Only a genuinely new outlet bumps distinct_source_count — a
            # second candidate from a source already in this cluster (either
            # from before this poll or earlier in this same batch, since the
            # set below is updated immediately) must not double-count.
            if source.id not in cluster_source_ids[matched_cluster.id]:
                matched_cluster.distinct_source_count += 1
                cluster_source_ids[matched_cluster.id].add(source.id)
        else:
            new_cluster = StoryCluster(
                headline=title,
                summary=snippet,
                article_count=1,
                distinct_source_count=1,
                first_seen_at=pub_date,
                last_updated_at=pub_date
            )
            session.add(new_cluster)
            await session.flush()
            cluster_source_ids[new_cluster.id] = {source.id}

            article = Article(
                source_id=source.id,
                url=link,
                url_hash=url_hash,
                title=title,
                snippet=snippet,
                content=content,
                word_count=word_count_val,
                image_url=image_url,
                video_url=video_url,
                media_type=media_type,
                video_is_short=video_is_short,
                video_duration_seconds=video_duration_seconds,
                author=author,
                published_at=pub_date,
                simhash=simhash_val,
                cluster_id=new_cluster.id
            )
            session.add(article)
            await session.flush()
            new_cluster.representative_article_id = article.id
            # Backfill rep_info for this brand-new article — it wasn't part
            # of the upfront prefetch (didn't exist yet), but a later
            # candidate in this same batch can still match against it as
            # recent_clusters grows. No query needed: this is the exact
            # object just built above.
            rep_info[article.id] = (
                SimpleNamespace(simhash=article.simhash, title=article.title, snippet=article.snippet),
                source.category,
            )
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

# --- Feed ranking redesign, piece 1: global importance (entity_stats) ---
# See app/services/entity_graph.py for canonicalization and the "Feed
# ranking redesign" design memory for the full reasoning. Both half-lives
# below are design-target approximations, not backtested constants — worth
# re-tuning once there's a few weeks of real entity_stats data to look at.
#
# mention_count_decayed tracks short-term "is this being talked about right
# now"; baseline_rate tracks the long-run normal rate for this entity. Both
# are normalized EMAs (rate_new = rate_old * decay + input * (1 - decay)),
# NOT raw decayed sums — that normalization is what keeps them on the same
# scale so an entity mentioned at a perfectly steady rate always settles to
# a ratio of ~1 regardless of its half-life; only their *responsiveness* to
# a recent change in rate differs; short reacts fast, baseline reacts slow,
# and it's that lag that makes a post-dormancy reappearance show up as a
# ratio > 1 rather than a rescaled duplicate of the same number.
ENTITY_MENTION_HALF_LIFE = timedelta(days=3)
ENTITY_BASELINE_HALF_LIFE = timedelta(days=75)
# A bit wider than the ~20min poll cadence (see infra/news-poll.timer) so a
# slightly late cycle doesn't miss a cluster that updated near the boundary.
ENTITY_STATS_LOOKBACK = timedelta(minutes=30)
# Floor under baseline_rate when computing the ratio, so a brand-new entity
# with a near-zero baseline can't produce a wildly inflated/undefined ratio
# off a single mention.
ENTITY_BASELINE_FLOOR = 0.05


async def _recompute_entity_stats(session: AsyncSession) -> None:
    """
    Tallies entity mentions from clusters updated within ENTITY_STATS_LOOKBACK,
    updates each canonical entity's mention_count_decayed/baseline_rate, and
    derives each touched cluster's entity_boost as the reactivation ratio
    (mention_count_decayed / baseline_rate) of its single most "spiking"
    entity — max, not average, so one important entity isn't diluted by
    otherwise-routine co-mentions. Shadow signal only: entity_boost is not
    read by /clusters yet (see StoryCluster.entity_boost's docstring).
    """
    now = utc_now()
    res = await session.execute(
        select(StoryCluster).where(
            StoryCluster.last_updated_at >= now - ENTITY_STATS_LOOKBACK,
            StoryCluster.entities.isnot(None),
        )
    )
    touched_clusters = res.scalars().all()
    if not touched_clusters:
        return

    mentions_this_cycle: Dict[str, int] = defaultdict(int)
    display_names: Dict[str, str] = {}
    cluster_entity_keys: Dict[int, Set[str]] = {}

    for cluster in touched_clusters:
        keys_for_cluster: Set[str] = set()
        entities = cluster.entities or {}
        for field, entity_type in (("persons", "person"), ("organizations", "organization"), ("locations", "location")):
            for raw_name in entities.get(field) or []:
                key = canonicalize_entity(raw_name, entity_type)
                if not key:
                    continue
                mentions_this_cycle[key] += 1
                display_names.setdefault(key, raw_name.strip())
                keys_for_cluster.add(key)
        cluster_entity_keys[cluster.id] = keys_for_cluster

    if not mentions_this_cycle:
        return

    res = await session.execute(
        select(EntityStat).where(EntityStat.entity_key.in_(mentions_this_cycle.keys()))
    )
    existing = {row.entity_key: row for row in res.scalars().all()}

    reactivation_ratio: Dict[str, float] = {}
    for key, new_mentions in mentions_this_cycle.items():
        row = existing.get(key)
        elapsed = (now - row.updated_at) if (row is not None and row.updated_at is not None) else timedelta(days=9999)
        prev_mentions = row.mention_count_decayed if row is not None else 0.0
        prev_baseline = row.baseline_rate if row is not None else 0.0

        new_mention_rate = ema_update(prev_mentions, elapsed, ENTITY_MENTION_HALF_LIFE, float(new_mentions))
        new_baseline_rate = ema_update(prev_baseline, elapsed, ENTITY_BASELINE_HALF_LIFE, float(new_mentions))
        reactivation_ratio[key] = new_mention_rate / max(new_baseline_rate, ENTITY_BASELINE_FLOOR)

        if row is None:
            session.add(EntityStat(
                entity_key=key,
                display_name=display_names[key],
                mention_count_decayed=new_mention_rate,
                baseline_rate=new_baseline_rate,
                last_mentioned_at=now,
                updated_at=now,
            ))
        else:
            row.mention_count_decayed = new_mention_rate
            row.baseline_rate = new_baseline_rate
            row.last_mentioned_at = now
            row.updated_at = now

    for cluster in touched_clusters:
        keys = cluster_entity_keys.get(cluster.id) or set()
        cluster.entity_boost = max((reactivation_ratio[k] for k in keys), default=0.0)


async def poll_all_sources(session: AsyncSession) -> int:
    got_lock = (await session.execute(select(func.pg_try_advisory_lock(POLL_LOCK_KEY)))).scalar()
    if not got_lock:
        logger.warning("Skipping poll: another poll_all_sources cycle is already running.")
        return 0

    try:
        res = await session.execute(select(Source))
        sources = res.scalars().all()
        total_new = 0

        async with CurlAsyncSession() as client:
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

        # Recompute the "All Stories" ranking score for every cluster, not
        # just ones touched this cycle — its recency-decay term moves for
        # every row on every cycle regardless of new ingestion. Pure column
        # arithmetic, no joins, cheap even at 100k+ clusters. See
        # StoryCluster.headline_score and app/main.py's use of it.
        await session.execute(text("""
            UPDATE story_clusters SET headline_score =
                distinct_source_count / POWER(
                    GREATEST(EXTRACT(EPOCH FROM (now() - last_updated_at)) / 3600.0, 0) + 2,
                    1.5
                )
        """))

        # Feed ranking redesign, piece 1: global importance. Same transaction
        # as the headline_score update above, so both are consistent as of
        # this commit. Shadow signal only — see _recompute_entity_stats.
        await _recompute_entity_stats(session)

        # Feed ranking redesign, piece 3: explore-slot bandit promotion
        # decisions. Same transaction as the two recomputes above, same
        # cadence. Shadow no more than piece 1 is — a promotion here takes
        # live effect in /clusters (see EXPLORE_PROMOTED_BOOST there).
        await recompute_explore_promotions(session)

        await session.commit()

        return total_new
    finally:
        # Defensive: if something above raised outside the per-source
        # try/except, the session's transaction could still be aborted here,
        # and an aborted session would reject even the unlock query itself.
        await session.rollback()
        await session.execute(select(func.pg_advisory_unlock(POLL_LOCK_KEY)))
