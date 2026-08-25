"""
Feed ranking redesign, piece 2 (per-user affinity). Two responsibilities:

1. record_engagement(): called when a read_events row gets its dwell/scroll
   close-out, updates the reading user's UserEntityAffinity rows for every
   entity in the story they just read.
2. score_clusters_for_user(): ranks a candidate set of clusters for a user
   by summed affinity across their entities — the ranking behind
   GET /clusters/for-you.

See app/services/entity_graph.py for canonicalization and app/services/
decay.py for the shared EMA math (same formula as piece 1's entity_stats,
different half-life). See the "Feed ranking redesign" design memory for why
per-user affinity is deliberately a separate, independent tab rather than a
reordering of "All Stories".
"""
from datetime import timedelta
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import StoryCluster, UserEntityAffinity, UserSourceFollow, utc_now
from app.services.decay import ema_update
from app.services.entity_graph import canonicalize_entity

# Shorter than entity_stats' 3-day mention half-life (see poller.py) —
# personal interest drifts faster than global newsworthiness, per the
# design memory.
USER_AFFINITY_HALF_LIFE = timedelta(days=5)

# Flat additive boost for "For You" clusters carrying an article from a
# source the user has starred (see UserSourceFollow), on top of whatever
# entity-affinity score the cluster already has. Deliberately a flat bonus,
# not multiplicative — a starred source with zero entity-affinity history
# should still visibly outrank an unstarred one, same as entity affinity
# degrades gracefully for a user with no read history yet. "All Stories"
# ranking (headline_score/entity_boost) is untouched by this — starring only
# affects /clusters/for-you, per the "User data to store" design session.
STARRED_SOURCE_BOOST = 2.0

_ENTITY_TYPE_FIELDS = (("persons", "person"), ("organizations", "organization"), ("locations", "location"))


def _entity_keys_for_cluster(cluster: StoryCluster) -> List[str]:
    entities = cluster.entities or {}
    keys = []
    for field, entity_type in _ENTITY_TYPE_FIELDS:
        for raw_name in entities.get(field) or []:
            key = canonicalize_entity(raw_name, entity_type)
            if key:
                keys.append(key)
    return keys


async def record_engagement(session: AsyncSession, user_id: str, cluster: StoryCluster, engagement_weight: float) -> None:
    """
    Update user_entity_affinity for every entity in `cluster`, weighted by
    `engagement_weight` (0..1-ish — see the read-events endpoint for how
    it's derived; a placeholder of 1.0 until piece 3's dwell-relative-to-
    article-length instrumentation exists, per the design memory).
    """
    keys = _entity_keys_for_cluster(cluster)
    if not keys:
        return

    now = utc_now()
    res = await session.execute(
        select(UserEntityAffinity).where(
            UserEntityAffinity.user_id == user_id,
            UserEntityAffinity.entity_key.in_(keys),
        )
    )
    existing = {row.entity_key: row for row in res.scalars().all()}

    for key in keys:
        row = existing.get(key)
        elapsed = (now - row.updated_at) if (row is not None and row.updated_at is not None) else timedelta(days=9999)
        prev = row.affinity_decayed if row is not None else 0.0
        new_value = ema_update(prev, elapsed, USER_AFFINITY_HALF_LIFE, engagement_weight)

        if row is None:
            session.add(UserEntityAffinity(
                user_id=user_id, entity_key=key, affinity_decayed=new_value, updated_at=now,
            ))
        else:
            row.affinity_decayed = new_value
            row.updated_at = now


async def score_clusters_for_user(
    session: AsyncSession, user_id: str, candidates: List[StoryCluster]
) -> Dict[int, float]:
    """
    affinity_score(cluster, u) = (sum of the user's affinity for every entity
    the cluster mentions) + (STARRED_SOURCE_BOOST if any of the cluster's
    articles come from a source the user has starred). Sum, not max, for the
    entity term — unlike piece 1's per-cluster boost, matching several
    things a user cares about should score higher than matching one; see the
    design memory. Both terms independently degrade to 0 for a user with no
    read/star history yet — this is what makes "For You" degrade gracefully
    to whatever order the caller otherwise falls back to for a brand-new
    user. Requires each candidate's `articles` (and `.source`) already
    eager-loaded by the caller — see /clusters/for-you's selectinload.
    """
    affinity_res = await session.execute(
        select(UserEntityAffinity).where(UserEntityAffinity.user_id == user_id)
    )
    affinity_by_key = {row.entity_key: row.affinity_decayed for row in affinity_res.scalars().all()}

    starred_res = await session.execute(
        select(UserSourceFollow.source_id).where(UserSourceFollow.user_id == user_id)
    )
    starred_source_ids = set(starred_res.scalars().all())

    scores = {}
    for cluster in candidates:
        entity_score = 0.0
        if affinity_by_key:
            keys = _entity_keys_for_cluster(cluster)
            entity_score = sum(affinity_by_key.get(k, 0.0) for k in keys)

        starred_boost = 0.0
        if starred_source_ids and any(a.source_id in starred_source_ids for a in cluster.articles):
            starred_boost = STARRED_SOURCE_BOOST

        scores[cluster.id] = entity_score + starred_boost
    return scores
