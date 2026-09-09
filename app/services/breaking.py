"""Detection + lifecycle for the "Breaking" slot — see app/models.py's
BreakingStory docstring and backend/docs/breaking-human-review-plan.md.

As of the human-review redesign, NO function in this module makes an LLM
call. detect_breaking_candidates and find_refresh_candidates are pure SQL:
they raise pending_review / pending-refresh rows for a human to act on via
app.admin_breaking, which is where judge_and_extract_beats actually runs
(on approve only). This module's job is purely "who's waiting on a human
right now" plus the non-LLM lifecycle (expiry) — same posture
poller.py's _enrich_new_crossings uses to keep paid calls out of the
ingestion transaction, except here the paid call moved out of the
automated cycle entirely, not just into its own committed-then-called step.

Entry points, all called once per poll cycle from
app.services.poller._poll_all_sources_locked (piggybacking that cycle
rather than a separate scheduler, since detection only needs to be as
fresh as distinct_source_count/became_multi_source_at, which that cycle
already maintains):

- expire_stale_breaking(session): ages `active` rows out. Run FIRST each
  cycle so an expiry frees a slot before detect_breaking_candidates counts
  how many are already occupied.
- expire_unreviewed_candidates(session): times out `pending_review` rows
  nobody acted on within EXPIRE_REVIEW_HOURS — a missed real story degrades
  to "no badge", never to a stale "pending" one sitting forever. Run
  alongside expire_stale_breaking, before the free-slot count.
- detect_breaking_candidates(session): finds newly-qualifying clusters and
  writes a pending_review row for each — no LLM call. A human decides via
  app.admin_breaking.
- find_refresh_candidates(session): finds active stories whose cluster
  gained enough sources since the last REVIEW (not generation) and writes
  a breaking_refresh_reviews row — no LLM call.

Unlike scripts/check_breaking_candidates.py, which reconstructs history
from articles because distinct_source_count is a live counter with no
memory of when it crossed a threshold, live detection doesn't need that
reconstruction: this runs every poll cycle, so a cluster is caught within
one cycle of actually crossing the gate — "now" IS effectively "when it
crossed".
"""
import logging
from dataclasses import dataclass
from typing import List

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Validated against 30 days of production data 2026-09-08
# (scripts/check_breaking_candidates.py): this pair produced ~1.3
# velocity-gate hits/day, with the Nepal flood (27 sources, 2.9h) and Delhi
# building collapse (27 sources, 5.3h) as the top two hits — the exact
# motivating stories. Re-measure with that script before moving either.
BREAKING_MIN_SOURCES = 20
BREAKING_WINDOW_HOURS = 24

# Up to two stories pinned at once — keeps the badge meaning "the one (or
# two) urgent things right now", not a section. See the 2026-09-08 decision.
MAX_ACTIVE_BREAKING = 2

# Expiry: "breaking" means the acute phase, not "still in the news" (see
# BreakingStory's docstring) — a story that keeps producing genuinely new
# beats for days (cluster 46759, the Nepal flood, still filed on day 6) is
# correctly demoted once EXPIRE_ABSOLUTE_HOURS passes, not kept pinned for
# as long as it stays newsworthy.
EXPIRE_STALE_BEAT_HOURS = 6
EXPIRE_ABSOLUTE_HOURS = 36

# How long a pending_review candidate waits on a human before it's treated
# as a miss rather than left occupying a slot indefinitely. Long enough for
# a push notification to actually be seen; short enough that a genuinely
# breaking story doesn't go unbadged all day. See
# backend/docs/breaking-human-review-plan.md.
EXPIRE_REVIEW_HOURS = 3

# Refresh review fires again once distinct_source_count has grown this much
# past the last REVIEW decision (approve or reject), rather than on a fixed
# timer — cost tracks actual new coverage, not clock time. See
# app.services.breaking_narrative and BreakingStory.last_reviewed_source_count.
REFRESH_SOURCE_DELTA = 5


@dataclass
class BreakingCandidate:
    cluster_id: int
    distinct_source_count: int
    hours_to_threshold: float


async def expire_stale_breaking(session: AsyncSession) -> int:
    """Flips `active` -> `expired` for rows that have gone quiet (no new
    beat in EXPIRE_STALE_BEAT_HOURS) or simply run their course
    (EXPIRE_ABSOLUTE_HOURS since promotion). Returns the number expired.

    Deliberately a single UPDATE rather than a Python loop over ORM rows —
    this runs every poll cycle and the set is tiny (<=2), but there is no
    reason to hydrate rows just to flip one column.
    """
    result = await session.execute(
        text(
            """
            UPDATE breaking_stories
            SET status = 'expired', updated_at = now()
            WHERE status = 'active'
              AND (
                    now() - COALESCE(last_beat_at, promoted_at) > make_interval(hours => :stale_hours)
                 OR now() - promoted_at > make_interval(hours => :absolute_hours)
              )
            RETURNING id
            """
        ),
        {"stale_hours": EXPIRE_STALE_BEAT_HOURS, "absolute_hours": EXPIRE_ABSOLUTE_HOURS},
    )
    expired_ids = [row[0] for row in result.fetchall()]
    if expired_ids:
        logger.info(f"[Breaking] expired {len(expired_ids)} stale row(s): {expired_ids}")
    return len(expired_ids)


async def expire_unreviewed_candidates(session: AsyncSession) -> int:
    """Flips `pending_review` -> `rejected` for candidates nobody reviewed
    within EXPIRE_REVIEW_HOURS. Terminal, same as an LLM-judged reject — the
    detector never re-asks about this cluster (see detect_breaking_candidates'
    NOT EXISTS check). Run before detect_breaking_candidates so a timed-out
    row frees its slot in the same cycle it expires, same ordering reason as
    expire_stale_breaking.
    """
    result = await session.execute(
        text(
            """
            UPDATE breaking_stories
            SET status = 'rejected', reviewed_at = now(), updated_at = now()
            WHERE status = 'pending_review'
              AND now() - promoted_at > make_interval(hours => :review_hours)
            RETURNING id
            """
        ),
        {"review_hours": EXPIRE_REVIEW_HOURS},
    )
    expired_ids = [row[0] for row in result.fetchall()]
    if expired_ids:
        logger.info(f"[Breaking] {len(expired_ids)} candidate(s) timed out unreviewed: {expired_ids}")
    return len(expired_ids)


async def detect_breaking_candidates(session: AsyncSession) -> List[BreakingCandidate]:
    """Returns clusters that just crossed the velocity gate and have no
    breaking_stories row yet (in ANY status — 'rejected' is terminal, see
    BreakingStory.status), capped to however many slots are actually free.
    'pending_review' counts as occupied here, same as 'active', so a
    candidate already waiting on a human isn't double-counted against the
    free-slot budget.

    Caller (poller.py) writes a pending_review row for each and notifies the
    admin — no LLM call happens in this module. See app.admin_breaking for
    where a human's approve/reject actually runs judge_and_extract_beats.
    """
    active_count_result = await session.execute(
        text("SELECT count(*) FROM breaking_stories WHERE status IN ('active', 'pending_review')")
    )
    active_count = active_count_result.scalar_one()
    free_slots = MAX_ACTIVE_BREAKING - active_count
    if free_slots <= 0:
        return []

    result = await session.execute(
        text(
            """
            SELECT c.id, c.distinct_source_count,
                   EXTRACT(EPOCH FROM (now() - c.became_multi_source_at)) / 3600.0 AS hours_to_threshold
            FROM story_clusters c
            WHERE c.distinct_source_count >= :min_sources
              AND c.became_multi_source_at IS NOT NULL
              AND now() - c.became_multi_source_at < make_interval(hours => :window_hours)
              AND NOT EXISTS (
                  SELECT 1 FROM breaking_stories b WHERE b.cluster_id = c.id
              )
            ORDER BY c.distinct_source_count DESC
            LIMIT :limit
            """
        ),
        {"min_sources": BREAKING_MIN_SOURCES, "window_hours": BREAKING_WINDOW_HOURS, "limit": free_slots},
    )
    return [
        BreakingCandidate(cluster_id=row.id, distinct_source_count=row.distinct_source_count,
                           hours_to_threshold=round(row.hours_to_threshold, 1))
        for row in result.fetchall()
    ]


async def find_refresh_candidates(session: AsyncSession) -> List[int]:
    """Active breaking_stories whose cluster has gained >= REFRESH_SOURCE_DELTA
    distinct sources since the last REVIEW decision (last_reviewed_source_count,
    NOT last_generated_source_count — a rejected/echo review still moves the
    goalposts so the same batch isn't re-raised forever), and that don't
    already have a pending refresh review. Returns cluster_ids; caller
    writes a breaking_refresh_reviews row and notifies the admin — no LLM
    call happens in this module. See app.admin_breaking for where approve
    actually runs the refresh pass.
    """
    result = await session.execute(
        text(
            """
            SELECT b.cluster_id
            FROM breaking_stories b
            JOIN story_clusters c ON c.id = b.cluster_id
            WHERE b.status = 'active'
              AND c.distinct_source_count >= COALESCE(b.last_reviewed_source_count, b.sources_at_promotion) + :delta
              AND NOT EXISTS (
                  SELECT 1 FROM breaking_refresh_reviews r
                  WHERE r.cluster_id = b.cluster_id AND r.status = 'pending'
              )
            """
        ),
        {"delta": REFRESH_SOURCE_DELTA},
    )
    return [row[0] for row in result.fetchall()]


async def _fetch_articles_for_prompt(session: AsyncSession, cluster_id: int, since=None) -> List[dict]:
    """Articles for one cluster in published_at order, shaped for
    breaking_narrative.format_articles_for_prompt. `since` restricts to
    articles newer than the last generation, for a refresh pass."""
    from app.models import Article, Source

    query = (
        select(Article.id, Article.published_at, Article.title, Source.name.label("source_name"))
        .join(Source, Source.id == Article.source_id)
        .where(Article.cluster_id == cluster_id)
        .order_by(Article.published_at.asc())
    )
    if since is not None:
        query = query.where(Article.published_at > since)
    result = await session.execute(query)
    return [
        {"id": row.id, "published_at": row.published_at, "title": row.title, "source_name": row.source_name}
        for row in result.fetchall()
    ]


async def process_breaking_cycle() -> None:
    """The full Breaking-slot cycle: expire stale/unreviewed rows, raise new
    candidates and refresh reviews for a human, notify the admin. Makes NO
    LLM call — see this module's docstring. Opens its own session and never
    raises, same never-block-ingestion posture as _enrich_new_crossings
    (kept even though there's no paid call left here, since a notification
    failure still shouldn't touch the poller's own transaction).
    """
    from app.database import AsyncSessionLocal
    from app.models import BreakingRefreshReview, BreakingStory
    from app.services.admin_notify import notify_admin_breaking_review

    try:
        async with AsyncSessionLocal() as session:
            expired = await expire_stale_breaking(session)
            timed_out = await expire_unreviewed_candidates(session)
            if expired or timed_out:
                await session.commit()

            candidates = await detect_breaking_candidates(session)
            for candidate in candidates:
                session.add(BreakingStory(
                    cluster_id=candidate.cluster_id,
                    status="pending_review",
                    sources_at_promotion=candidate.distinct_source_count,
                    hours_to_threshold=candidate.hours_to_threshold,
                ))
                logger.info(
                    f"[Breaking] cluster {candidate.cluster_id} flagged for review "
                    f"({candidate.distinct_source_count} sources)"
                )
            if candidates:
                await session.commit()

            refresh_cluster_ids = await find_refresh_candidates(session)
            for cluster_id in refresh_cluster_ids:
                source_count = await session.execute(
                    text("SELECT distinct_source_count FROM story_clusters WHERE id = :cid"),
                    {"cid": cluster_id},
                )
                session.add(BreakingRefreshReview(
                    cluster_id=cluster_id,
                    status="pending",
                    source_count_at_review=source_count.scalar_one(),
                ))
                logger.info(f"[Breaking] cluster {cluster_id} flagged for refresh review")
            if refresh_cluster_ids:
                await session.commit()

            if candidates or refresh_cluster_ids:
                await notify_admin_breaking_review(
                    session, new_count=len(candidates), refresh_count=len(refresh_cluster_ids)
                )
    except Exception as e:
        logger.error(f"[Breaking] cycle failed: {e}")
