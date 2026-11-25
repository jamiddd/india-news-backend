"""Shared story-cluster text search: the same ILIKE-on-headline/summary query
used by the public GET /search endpoint (main.py) and the Explainers admin
panel's source picker (admin_explainers.py), factored out so the two never
drift apart.
"""
from __future__ import annotations

from typing import Callable, Optional

from sqlalchemy import Column, Integer, MetaData, Table, Text, desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Article, StoryCluster
from app.services.topic_filters import keyword_regex, location_keywords_for_query, repeated_keyword_regex

# Search text lives in its own narrow table, kept in sync by a trigger on
# story_clusters (scripts/add_cluster_search_table.py). The trigram indexes that
# make ILIKE fast are on this table, not on story_clusters: headline_score is
# rewritten on thousands of story_clusters rows every poll cycle, and an update
# that can't be HOT writes a new entry into EVERY index on the row, which with
# two GIN trigram indexes meant ~28 KB of WAL per rewritten row.
#
# Deliberately NOT part of app.database.Base: main.py runs Base.metadata.create_all
# at startup, which would create an empty, trigger-less table if the code were
# deployed before the migration -- and search would silently return nothing.
# Declared on a private MetaData instead, so deploying before the migration
# fails loudly ("relation cluster_search does not exist") rather than quietly.
_search_metadata = MetaData()
cluster_search = Table(
    "cluster_search",
    _search_metadata,
    Column("cluster_id", Integer, primary_key=True),
    Column("headline", Text),
    Column("summary", Text),
)


async def search_clusters(
    db: AsyncSession,
    q: str,
    *,
    limit: int = 20,
    cursor: Optional[int] = None,
    apply_gate: Optional[Callable] = None,
    source_id: Optional[int] = None,
) -> list[StoryCluster]:
    """Up to `limit` + 1 clusters (so callers can detect "has more") whose
    headline or summary matches `q`, newest first, with articles/sources
    eager-loaded. `apply_gate`, when given, is main.py's apply_feed_gate —
    the admin source picker omits it, since an editor picking sources should
    see everything, not just what the public feed gate allows through.

    `source_id`, when given, restricts results to clusters carrying an
    article from that source — same subquery shape as GET /clusters'
    `source_id` filter (main.py) — powering SourceFeedScreen's search icon
    (search within this outlet only)."""
    pattern = f"%{q}%"
    place_keywords = location_keywords_for_query(q)
    if place_keywords:
        # A place or region ("Guwahati", "North east"): only stories where it
        # is central -- in the headline or an article title, or repeated in
        # the summary -- not ones that name it once in passing.
        kw = keyword_regex(place_keywords)
        match = or_(
            StoryCluster.id.in_(
                select(cluster_search.c.cluster_id).where(
                    or_(
                        cluster_search.c.headline.op("~*")(kw),
                        cluster_search.c.summary.op("~*")(repeated_keyword_regex(place_keywords)),
                    )
                )
            ),
            StoryCluster.id.in_(select(Article.cluster_id).where(Article.title.op("~*")(kw))),
        )
    else:
        match = StoryCluster.id.in_(
            select(cluster_search.c.cluster_id).where(
                or_(cluster_search.c.headline.ilike(pattern), cluster_search.c.summary.ilike(pattern))
            )
        )
    query = (
        select(StoryCluster)
        .options(selectinload(StoryCluster.articles).selectinload(Article.source))
        .where(match)
        .order_by(desc(StoryCluster.last_updated_at), desc(StoryCluster.id))
    )
    if apply_gate is not None:
        query = apply_gate(query)
    if source_id is not None:
        query = query.where(
            StoryCluster.id.in_(select(Article.cluster_id).where(Article.source_id == source_id))
        )
    if cursor:
        query = query.where(StoryCluster.id < cursor)
    query = query.limit(limit + 1)
    result = await db.execute(query)
    return list(result.scalars().all())
