"""Shared story-cluster text search: the same ILIKE-on-headline/summary query
used by the public GET /search endpoint (main.py) and the Explainers admin
panel's source picker (admin_explainers.py), factored out so the two never
drift apart.
"""
from __future__ import annotations

from typing import Callable, Optional

from sqlalchemy import desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Article, StoryCluster
from app.services.topic_filters import keyword_regex, location_keywords_for_query, repeated_keyword_regex


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
            StoryCluster.headline.op("~*")(kw),
            StoryCluster.id.in_(select(Article.cluster_id).where(Article.title.op("~*")(kw))),
            StoryCluster.summary.op("~*")(repeated_keyword_regex(place_keywords)),
        )
    else:
        match = or_(StoryCluster.headline.ilike(pattern), StoryCluster.summary.ilike(pattern))
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
