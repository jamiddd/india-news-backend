"""Detection + lifecycle for the "Breaking" slot — see app/models.py's
BreakingStory docstring and the 2026-09-08 planning session.

Two entry points, both called once per poll cycle from
app.services.poller._poll_all_sources_locked (piggybacking that cycle
rather than a separate scheduler, since detection only needs to be as
fresh as distinct_source_count/became_multi_source_at, which that cycle
already maintains):

- expire_stale_breaking(session): ages `active` rows out. Run FIRST each
  cycle so an expiry frees a slot before detect_breaking_candidates counts
  how many are already occupied.
- detect_breaking_candidates(session): finds newly-qualifying clusters and
  returns them for the caller to run the LLM pass over (kept OUT of this
  module — see app.services.breaking_narrative — so this stays pure SQL/DB
  and the paid call stays in its own committed-then-called step, same
  split as poller.py's _enrich_new_crossings).

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

# Refresh fires again once distinct_source_count has grown this much past
# the last successful generation, rather than on a fixed timer — cost
# tracks actual new coverage, not clock time. See app.services.breaking_narrative.
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


async def detect_breaking_candidates(session: AsyncSession) -> List[BreakingCandidate]:
    """Returns clusters that just crossed the velocity gate and have no
    breaking_stories row yet (in ANY status — 'rejected' is terminal, see
    BreakingStory.status), capped to however many slots are actually free.

    Caller (poller.py) is responsible for running the LLM developing-vs-echo
    pass on each candidate and writing the resulting breaking_stories row
    (status='active' or 'rejected') — kept out of this function so a slow/
    failed LLM call can never block or roll back the poll cycle's own
    transaction, same split as _enrich_new_crossings.
    """
    active_count_result = await session.execute(
        text("SELECT count(*) FROM breaking_stories WHERE status = 'active'")
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
    distinct sources since the last successful generation — the append-only
    refresh trigger. Returns cluster_ids; caller re-runs the LLM pass and
    extends `beats`."""
    result = await session.execute(
        text(
            """
            SELECT b.cluster_id
            FROM breaking_stories b
            JOIN story_clusters c ON c.id = b.cluster_id
            WHERE b.status = 'active'
              AND c.distinct_source_count >= COALESCE(b.last_generated_source_count, b.sources_at_promotion) + :delta
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
    """The full Breaking-slot cycle: expire stale rows, judge new
    candidates, refresh active ones whose clusters gained enough new
    sources. Opens its own session and never raises — called from
    poller.py AFTER that cycle's own commit, same split and same
    never-block-ingestion posture as _enrich_new_crossings, since this
    makes paid LLM calls.
    """
    from app.database import AsyncSessionLocal
    from app.models import BreakingStory, utc_now
    from app.services.breaking_narrative import judge_and_extract_beats
    from app.services.breaking_narrative import BreakingNarrativeError

    try:
        async with AsyncSessionLocal() as session:
            expired = await expire_stale_breaking(session)
            if expired:
                await session.commit()

            candidates = await detect_breaking_candidates(session)
            for candidate in candidates:
                articles = await _fetch_articles_for_prompt(session, candidate.cluster_id)
                if not articles:
                    continue
                try:
                    result = await judge_and_extract_beats(articles)
                except BreakingNarrativeError as e:
                    logger.error(f"[Breaking] judge pass failed for cluster {candidate.cluster_id}: {e}")
                    continue

                if not result.get("developing") or not result.get("beats"):
                    session.add(BreakingStory(
                        cluster_id=candidate.cluster_id,
                        status="rejected",
                        sources_at_promotion=candidate.distinct_source_count,
                        hours_to_threshold=candidate.hours_to_threshold,
                    ))
                    logger.info(f"[Breaking] cluster {candidate.cluster_id} rejected (not developing)")
                else:
                    latest_beat_time = max(a["published_at"] for a in articles)
                    session.add(BreakingStory(
                        cluster_id=candidate.cluster_id,
                        status="active",
                        title=result.get("title") or None,
                        beats=result["beats"],
                        last_beat_at=latest_beat_time,
                        last_generated_at=utc_now(),
                        last_generated_source_count=candidate.distinct_source_count,
                        sources_at_promotion=candidate.distinct_source_count,
                        hours_to_threshold=candidate.hours_to_threshold,
                    ))
                    logger.info(
                        f"[Breaking] promoted cluster {candidate.cluster_id} "
                        f"({candidate.distinct_source_count} sources, {len(result['beats'])} beats)"
                    )
                await session.commit()

            refresh_ids = await find_refresh_candidates(session)
            for cluster_id in refresh_ids:
                row_result = await session.execute(
                    select(BreakingStory).where(BreakingStory.cluster_id == cluster_id)
                )
                row = row_result.scalar_one_or_none()
                if row is None:
                    continue
                new_articles = await _fetch_articles_for_prompt(session, cluster_id, since=row.last_generated_at)
                if not new_articles:
                    continue
                try:
                    result = await judge_and_extract_beats(new_articles, existing_beats=row.beats or [])
                except BreakingNarrativeError as e:
                    logger.error(f"[Breaking] refresh pass failed for cluster {cluster_id}: {e}")
                    continue

                merged_beats = (row.beats or []) + result.get("beats", [])
                row.beats = merged_beats
                row.last_generated_at = utc_now()
                cluster_row = await session.execute(
                    text("SELECT distinct_source_count FROM story_clusters WHERE id = :cid"),
                    {"cid": cluster_id},
                )
                row.last_generated_source_count = cluster_row.scalar_one()
                if result.get("beats"):
                    row.last_beat_at = max(a["published_at"] for a in new_articles)
                logger.info(f"[Breaking] refreshed cluster {cluster_id} (+{len(result.get('beats', []))} beats)")
                await session.commit()
    except Exception as e:
        logger.error(f"[Breaking] cycle failed: {e}")
