"""
Feed ranking redesign, piece 3: the explore-slot bandit. Deliberately
injects a low-source-count candidate story into a fixed position (2, per
the design memory — simplest option, appropriate for the current app/user-
base size) of logged-in users' "All Stories" first page, and promotes it to
a real, live ranking boost if it earns real engagement across enough
exposures. This is the actual fix for stories piece 1's entity_boost alone
can't rescue: something with only 1-2 sources that never accumulates enough
corroboration to rank, no matter how important.

See app/models.py's ExploreExposure/StoryCluster.explore_status, the
GET /clusters endpoint's optional user_id param (app/main.py), and the
poll-cycle recompute wired into app/services/poller.py.
"""
from datetime import timedelta
from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Article, ExploreExposure, ReadEvent, StoryCluster, utc_now
from app.services.engagement import engagement as _engagement

# A cluster is eligible as a candidate only while it's this fresh and this
# under-covered — an old or already-well-corroborated story doesn't need
# the bandit's help (piece 1's entity_boost is the mechanism for stories
# that *do* have some corroboration but got buried by recency decay).
CANDIDATE_MAX_SOURCES = 2
CANDIDATE_MAX_AGE = timedelta(hours=48)

# Fixed slot (index 1 = visual position 2) per the design decision — see
# the "Feed ranking redesign" memory: simplest option, cleanest signal,
# appropriate for the current app/user-base size.
EXPLORE_SLOT_POSITION = 2

MIN_EXPOSURE_COUNT = 20
# One-sided ~95% confidence bound multiplier (z=1.645) — see
# recompute_explore_promotions: a candidate is promoted only when its
# engagement mean, MINUS this many standard errors, still clears the
# baseline*threshold bar. Guards against promoting off noise from a
# handful of unusually engaged readers at low exposure counts.
CONFIDENCE_Z = 1.645
ENGAGEMENT_THRESHOLD_MULTIPLIER = 1.3

# Multiplies effective_score for a promoted cluster in GET /clusters — high
# enough that a 1-2-source promoted story can compete with (and usually
# beat) a several-source story's headline_score, without hardcoding a
# literal position. Tunable; not backtested yet (see design memory — same
# caveat as piece 1's decay half-lives).
EXPLORE_PROMOTED_BOOST = 8.0

# How many past cycles' worth of ordinary (non-explore) read_events to
# sample when estimating baseline_mean_engagement. A cap, not a time
# window — keeps the recompute cheap regardless of total read_events volume.
BASELINE_SAMPLE_SIZE = 500

# Used when a candidate/read cluster has no scrapable article content to
# estimate length from (representative article missing/empty) — a
# deliberately modest default so an unknown-length story isn't penalized
# for a data gap, but also isn't given credit it hasn't earned.
DEFAULT_WORD_COUNT = 300


async def _estimate_word_count(session: AsyncSession, cluster: StoryCluster) -> int:
    """Best-effort word count for engagement's dwell-time baseline: the
    cluster's representative article's scraped content, falling back to its
    summary, falling back to a fixed default. Not exact — see
    DEFAULT_WORD_COUNT."""
    if cluster.representative_article_id is not None:
        res = await session.execute(
            select(Article.content).where(Article.id == cluster.representative_article_id)
        )
        content = res.scalar_one_or_none()
        if content:
            return max(len(content.split()), 1)
    if cluster.summary:
        return max(len(cluster.summary.split()), 1)
    return DEFAULT_WORD_COUNT


async def pick_candidate(session: AsyncSession) -> Optional[StoryCluster]:
    """
    One eligible, not-yet-decided candidate, preferring whichever has the
    fewest exposures so far — spreads exposure across the candidate pool
    instead of hammering the first one found every cycle.
    """
    exposure_counts = (
        select(ExploreExposure.cluster_id, func.count(ExploreExposure.id).label("n"))
        .group_by(ExploreExposure.cluster_id)
        .subquery()
    )
    res = await session.execute(
        select(StoryCluster)
        .outerjoin(exposure_counts, exposure_counts.c.cluster_id == StoryCluster.id)
        .where(
            StoryCluster.explore_status == "pending",
            StoryCluster.distinct_source_count <= CANDIDATE_MAX_SOURCES,
            StoryCluster.first_seen_at >= utc_now() - CANDIDATE_MAX_AGE,
        )
        .order_by(func.coalesce(exposure_counts.c.n, 0).asc(), StoryCluster.first_seen_at.desc())
        .limit(1)
    )
    return res.scalar_one_or_none()


async def record_exposure(session: AsyncSession, cluster_id: int, user_id: str) -> None:
    session.add(ExploreExposure(cluster_id=cluster_id, user_id=user_id, position=EXPLORE_SLOT_POSITION))


async def _estimate_baseline_mean_engagement(session: AsyncSession) -> float:
    """
    Approximate baseline_mean_engagement from a sample of ordinary
    (non-explore-tagged) closed read events. A true position-2-specific
    baseline would need the client to report each read's list position —
    real client work not yet done (see design memory) — so this uses a
    simpler global mean as a v1 approximation, upgradeable later.
    """
    not_explore_exposed = (
        ~select(ExploreExposure.id)
        .where(
            ExploreExposure.cluster_id == ReadEvent.cluster_id,
            ExploreExposure.user_id == ReadEvent.user_id,
        )
        .exists()
    )
    res = await session.execute(
        select(ReadEvent.dwell_ms, ReadEvent.scroll_depth_pct, ReadEvent.cluster_id)
        .where(ReadEvent.dwell_ms.isnot(None), not_explore_exposed)
        .order_by(ReadEvent.updated_at.desc())
        .limit(BASELINE_SAMPLE_SIZE)
    )
    rows = res.all()
    if not rows:
        return 0.0

    # Word counts vary per cluster; cache per cluster_id within this call
    # rather than re-querying per row.
    word_counts: dict[int, int] = {}
    engagements: List[float] = []
    for dwell_ms, scroll_depth_pct, cluster_id in rows:
        if cluster_id not in word_counts:
            cluster_res = await session.execute(select(StoryCluster).where(StoryCluster.id == cluster_id))
            cluster = cluster_res.scalar_one_or_none()
            word_counts[cluster_id] = await _estimate_word_count(session, cluster) if cluster else DEFAULT_WORD_COUNT
        engagements.append(_engagement(dwell_ms, scroll_depth_pct, word_counts[cluster_id]))

    return sum(engagements) / len(engagements)


async def recompute_explore_promotions(session: AsyncSession) -> None:
    """
    For every "pending" candidate with >= MIN_EXPOSURE_COUNT exposures,
    scores its engagement (0 for exposures never opened) and either
    promotes it (real, live ranking boost — see EXPLORE_PROMOTED_BOOST and
    its use in GET /clusters) or rejects it (frees the candidate pool),
    based on a confidence-bounded comparison against a baseline mean
    engagement — not a bare mean, since MIN_EXPOSURE_COUNT is small enough
    for a couple of outlier readers to swing a raw average. See the "Feed
    ranking redesign" design memory.
    """
    res = await session.execute(
        select(StoryCluster.id, func.count(ExploreExposure.id).label("n"))
        .join(ExploreExposure, ExploreExposure.cluster_id == StoryCluster.id)
        .where(StoryCluster.explore_status == "pending")
        .group_by(StoryCluster.id)
        .having(func.count(ExploreExposure.id) >= MIN_EXPOSURE_COUNT)
    )
    ready_cluster_ids = [row.id for row in res.all()]
    if not ready_cluster_ids:
        return

    baseline = await _estimate_baseline_mean_engagement(session)
    threshold = baseline * ENGAGEMENT_THRESHOLD_MULTIPLIER

    for cluster_id in ready_cluster_ids:
        cluster_res = await session.execute(select(StoryCluster).where(StoryCluster.id == cluster_id))
        cluster = cluster_res.scalar_one_or_none()
        if cluster is None:
            continue
        word_count = await _estimate_word_count(session, cluster)

        exposures_res = await session.execute(
            select(ExploreExposure.user_id, ExploreExposure.exposed_at)
            .where(ExploreExposure.cluster_id == cluster_id)
        )
        exposures = exposures_res.all()

        engagements = []
        for user_id, exposed_at in exposures:
            read_res = await session.execute(
                select(ReadEvent.dwell_ms, ReadEvent.scroll_depth_pct)
                .where(
                    ReadEvent.user_id == user_id,
                    ReadEvent.cluster_id == cluster_id,
                    ReadEvent.opened_at >= exposed_at,
                )
                .order_by(ReadEvent.opened_at.desc())
                .limit(1)
            )
            read_row = read_res.first()
            if read_row is None:
                engagements.append(0.0)  # exposed, never opened
            else:
                engagements.append(_engagement(read_row.dwell_ms, read_row.scroll_depth_pct, word_count))

        n = len(engagements)
        mean = sum(engagements) / n
        variance = sum((e - mean) ** 2 for e in engagements) / n
        std_error = (variance / n) ** 0.5
        lower_bound = mean - CONFIDENCE_Z * std_error

        cluster.explore_status = "promoted" if lower_bound > threshold else "rejected"
