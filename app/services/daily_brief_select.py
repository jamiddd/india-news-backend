"""Story selection for the Daily Brief: which of yesterday's stories make the
10 slots.

Slots 1-3 are the "most covered" stories of the day regardless of topic; the
remaining slots each take the best story from one core category, so the brief
is not just the top headlines. Only headlines are gathered here (plus the
titles of the articles under each story) — no article bodies, no explainers.

"Yesterday" is the Asia/Kolkata calendar day, and coverage is counted from the
articles PUBLISHED inside that window (not the cluster's lifetime
distinct_source_count), so a story that kept running for days is scored on
what it did yesterday. headline_score is not used for ranking: it decays with
age, so it is meaningless for a story being judged the next morning.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Article, Source, StoryCluster
from app.services.feed_gate import gate_min_sources, listing_age_anchor
from app.services.topic_filters import CONTENT_GATED_CATEGORIES

IST = ZoneInfo("Asia/Kolkata")

MOST_COVERED_SLOTS = 3
MAX_SLOTS = 10
# One slot each, in this order (also the tie-break when a story's sources
# split evenly across categories).
CORE_CATEGORIES = ["national", "politics", "business", "world", "tech", "sports", "entertainment"]
# Used, in order, to fill a core category that had no eligible story.
FALLBACK_CATEGORIES = ["health", "northeast", "education"]
# How many of the day's best-covered clusters to look at. The three slots and
# seven categories are always satisfied from far fewer.
CANDIDATE_LIMIT = 400
# A pair of stories sharing at least this fraction of their named entities are
# treated as the same event.
ENTITY_OVERLAP_DUPLICATE = 0.5
MAX_TITLES_PER_STORY = 3
# A story normally has about as many articles as outlets covering it (2026-09-25
# data: healthy top stories had 12-49 articles from 12-26 outlets, at most 2 per
# outlet). Clusters far above that are runaway merges that absorbed unrelated
# coverage — the BRICS "story" of that day held 1,721 articles and 123 videos,
# most of them unrelated, and opening it in the app downloaded ~5 MB. They are
# not a story worth leading a brief with, so they are skipped. Healthy stories
# sat at <= 2.0 and runaways at >= 4.4, so 3 separates them with room to spare.
MAX_ARTICLES_PER_OUTLET = 3.0
# Same freshness rule the Top Headlines listing uses (feed_gate.listing_age_anchor
# against LISTING_MAX_AGE), tightened to the brief's own frame: a story must have
# become corroborated no earlier than this long before the day being summarised.
# Without it, old clusters that keep absorbing new articles (the poller refreshes a
# cluster's last_updated_at on every join, so a busy one never ages out of matching)
# counted as "covered yesterday" — the BRICS cluster was 25 days old — while Top
# Headlines never showed them because they fail this same age check.
MAX_STORY_AGE_BEFORE_WINDOW = timedelta(days=1)


@dataclass
class BriefStory:
    cluster_id: int
    headline: str
    category: str
    source_count: int
    image_url: Optional[str]
    titles: list[str] = field(default_factory=list)
    slot_kind: str = "category"  # "most_covered" | "category"


def brief_window(brief_date: date) -> tuple[datetime, datetime]:
    """[start, end) of the calendar day BEFORE brief_date, in IST."""
    day = brief_date - timedelta(days=1)
    start = datetime.combine(day, time.min, tzinfo=IST)
    return start, start + timedelta(days=1)


def _keyword_pattern(category: str) -> Optional[re.Pattern]:
    keywords = CONTENT_GATED_CATEGORIES.get(category)
    if not keywords:
        return None
    return re.compile(r"\b(" + "|".join(re.escape(k) for k in keywords) + r")\b", re.IGNORECASE)


_GATE_PATTERNS = {cat: _keyword_pattern(cat) for cat in CONTENT_GATED_CATEGORIES}


def is_runaway_cluster(article_count: Optional[int], distinct_source_count: Optional[int]) -> bool:
    """True for a cluster with implausibly many articles per outlet — see
    MAX_ARTICLES_PER_OUTLET."""
    return (article_count or 0) > MAX_ARTICLES_PER_OUTLET * max(distinct_source_count or 1, 1)


def _entity_set(entities) -> set[str]:
    if not isinstance(entities, dict):
        return set()
    out: set[str] = set()
    for values in entities.values():
        if isinstance(values, list):
            out.update(str(v).strip().lower() for v in values if v)
    return out


def _same_event(a: set[str], b: set[str]) -> bool:
    if not a or not b:
        return False
    return len(a & b) / min(len(a), len(b)) >= ENTITY_OVERLAP_DUPLICATE


async def select_stories(session: AsyncSession, brief_date: date) -> list[BriefStory]:
    """The brief's stories for brief_date, most-covered first, then one per
    category. May return fewer than MAX_SLOTS on a thin news day."""
    start, end = brief_window(brief_date)
    in_window = (Article.published_at >= start) & (Article.published_at < end) & Article.cluster_id.isnot(None)

    n_sources = func.count(func.distinct(Article.source_id))
    ranked = (
        await session.execute(
            select(Article.cluster_id, n_sources.label("n"))
            .join(StoryCluster, StoryCluster.id == Article.cluster_id)
            .where(in_window)
            .where(listing_age_anchor() >= start - MAX_STORY_AGE_BEFORE_WINDOW)
            .group_by(Article.cluster_id)
            .having(n_sources >= gate_min_sources())
            .order_by(n_sources.desc(), Article.cluster_id.desc())
            .limit(CANDIDATE_LIMIT)
        )
    ).all()
    if not ranked:
        return []
    coverage = {row.cluster_id: row.n for row in ranked}

    clusters = {
        c.id: c
        for c in (
            await session.execute(select(StoryCluster).where(StoryCluster.id.in_(list(coverage))))
        ).scalars()
    }
    article_rows = (
        await session.execute(
            select(
                Article.cluster_id, Article.source_id, Article.title,
                Article.image_url, Article.image_width, Source.category,
            )
            .join(Source, Source.id == Article.source_id)
            .where(in_window, Article.cluster_id.in_(list(coverage)))
            .order_by(Article.published_at.desc())
        )
    ).all()

    titles: dict[int, list[str]] = {}
    images: dict[int, tuple[int, str]] = {}
    cat_sources: dict[int, dict[str, set[int]]] = {}
    for row in article_rows:
        cid = row.cluster_id
        if row.title and row.title not in titles.setdefault(cid, []):
            titles[cid].append(row.title)
        if row.image_url:
            width = row.image_width or 0
            if cid not in images or width > images[cid][0]:
                images[cid] = (width, row.image_url)
        cat_sources.setdefault(cid, {}).setdefault(row.category or "general", set()).add(row.source_id)

    def category_of(cid: int) -> Optional[str]:
        """Category holding the most distinct sources. A category with a
        content gate only counts if the story's own text mentions something
        on-topic — the same rule the category tabs apply."""
        text = " ".join([clusters[cid].headline] + titles.get(cid, []))
        best: Optional[str] = None
        best_key: tuple[int, int] = (0, 0)
        for cat, sources in cat_sources.get(cid, {}).items():
            pattern = _GATE_PATTERNS.get(cat)
            if pattern is not None and not pattern.search(text):
                continue
            priority = -CORE_CATEGORIES.index(cat) if cat in CORE_CATEGORIES else -len(CORE_CATEGORIES)
            key = (len(sources), priority)
            if key > best_key:
                best, best_key = cat, key
        return best

    # Best first: most sources yesterday, then the cluster's own score, then
    # the newest cluster (deterministic).
    order = sorted(
        (
            cid for cid in coverage
            if cid in clusters
            and not is_runaway_cluster(clusters[cid].article_count, clusters[cid].distinct_source_count)
        ),
        key=lambda cid: (-coverage[cid], -(clusters[cid].headline_score or 0.0), -cid),
    )
    categories = {cid: category_of(cid) for cid in order}
    entities = {cid: _entity_set(clusters[cid].entities) for cid in order}

    chosen: list[BriefStory] = []
    chosen_entities: list[set[str]] = []

    def take(cid: int, kind: str) -> None:
        cluster = clusters[cid]
        chosen.append(
            BriefStory(
                cluster_id=cid,
                headline=cluster.headline,
                category=categories[cid] or "general",
                source_count=coverage[cid],
                image_url=images.get(cid, (0, None))[1],
                titles=titles.get(cid, [])[:MAX_TITLES_PER_STORY],
                slot_kind=kind,
            )
        )
        chosen_entities.append(entities[cid])

    def eligible(cid: int) -> bool:
        if any(s.cluster_id == cid for s in chosen):
            return False
        return not any(_same_event(entities[cid], seen) for seen in chosen_entities)

    for cid in order:
        if len(chosen) >= MOST_COVERED_SLOTS:
            break
        if eligible(cid):
            take(cid, "most_covered")

    def fill(category: str) -> bool:
        for cid in order:
            if categories[cid] == category and eligible(cid):
                take(cid, "category")
                return True
        return False

    missed = 0
    for category in CORE_CATEGORIES:
        if len(chosen) < MAX_SLOTS and not fill(category):
            missed += 1
    # Fallback categories only stand in for a core category with no story.
    for category in FALLBACK_CATEGORIES:
        if missed == 0 or len(chosen) >= MAX_SLOTS:
            break
        if fill(category):
            missed -= 1

    return chosen
