"""The multi-source feed gate.

The app's premise is comparative coverage, and with the gate on a story
earns a place in the feed by being covered by FEED_MIN_DISTINCT_SOURCES
distinct outlets. Single-source RSS items keep being ingested — they are the
raw material clustering works on, and any of them may earn its way in later
— but they are not shown.

Lives here rather than in main.py so every surface can apply the same rule
from one definition, and so it is testable without importing the whole
FastAPI app. See docs/clustering-rework-handoff.md for why recall, not
precision, is the risk this gate exposes: ~46% of genuinely related pairs
are still not merged, and under this gate those stories are invisible rather
than merely buried.
"""
from datetime import timedelta

from sqlalchemy import func

from app.config import settings
from app.models import StoryCluster, utc_now


# Clusters older than this never surface in listings, in either feed. Added
# after finding stale singleton crypto clusters (some from 2022) ranking as
# if fresh: their last_updated_at had been bulk-touched to "now" by an
# out-of-band write unrelated to any real new coverage, which both sorted
# them above genuinely current stories in category tabs (ordered by
# last_updated_at) and inflated their headline_score's recency-decay term in
# the "All" feed. Lives here beside the gate because it answers the same
# question — may this cluster be shown — and because scripts outside the
# FastAPI app need it too (notifications).
LISTING_MAX_AGE = timedelta(days=4)


def listing_age_anchor():
    """The timestamp a cluster's listing age is measured from.

    COALESCE(became_multi_source_at, first_seen_at): for a corroborated
    story, its clock starts when it earned its second outlet, not when its
    first article appeared. Those two moments have a median gap of ~4 hours
    for stories that stop at two outlets, so anchoring on first_seen_at
    spends a large slice of the window before the story was even
    presentable as comparative coverage. Falls back to first_seen_at for
    single-source clusters, which is exactly the pre-gate behaviour — and
    for rows the migration backfilled, since it backfilled from
    first_seen_at.

    Both halves are set once and never rewritten, so this inherits
    first_seen_at's immunity to the last_updated_at drift described above.
    See models.py StoryCluster.became_multi_source_at.
    """
    return func.coalesce(
        StoryCluster.became_multi_source_at, StoryCluster.first_seen_at
    )


def gate_min_sources() -> int:
    """The effective threshold for a listing query.

    Returns 1 when the gate is off — a floor every cluster clears, since
    distinct_source_count is never below 1 — so callers that need a plain
    integer for raw SQL can interpolate this unconditionally instead of
    branching.
    """
    if not settings.FEED_GATE_ENABLED:
        return 1
    return settings.FEED_MIN_DISTINCT_SOURCES


def apply_feed_gate(query):
    """Restrict a cluster listing to corroborated stories.

    Applied to every listing — default feed, category tabs, source filter,
    search, for-you, related stories — so the app cannot show on one surface
    what it hides on another. NOT applied to detail-by-id: a deep link, a
    notification, or a saved story from before the gate must still open.

    A no-op unless FEED_GATE_ENABLED, so this ships ahead of the decision to
    turn it on. See docs/multi-source-feed-plan.md §5.A.
    """
    if not settings.FEED_GATE_ENABLED:
        return query
    return query.where(
        StoryCluster.distinct_source_count >= settings.FEED_MIN_DISTINCT_SOURCES
    )


def gate_cache_marker() -> str:
    """Cache-key fragment identifying the gate's state.

    Listing responses are cached in Redis for CACHE_TTL_SECONDS. Without
    this in the key, flipping the flag keeps serving pre-flip pages for up
    to five minutes — ungated stories in a gated feed, or the reverse —
    which is exactly the window in which someone is checking whether the
    flag worked.
    """
    return "g1" if settings.FEED_GATE_ENABLED else "g0"


def notifiable_clauses():
    """What a cluster must be before it is worth waking someone's phone for.

    Applied to BOTH modes, and deliberately not conditional on
    FEED_GATE_ENABLED. A push is the most intrusive surface the app has, so
    the bar is the product's own thesis — corroborated coverage — whether or
    not the in-app feed is currently gated. For breaking this codifies
    behaviour that was already true but only as an emergent property of the
    threshold constant (see the module docstring); for the daily digest it is
    a genuine change.

    The age bound is the same one every listing uses. The daily digest had
    none at all, which left it exposed to precisely the stale-cluster class
    LISTING_MAX_AGE exists to stop — a cluster whose last_updated_at was
    bulk-touched to "now" scores as if it were breaking news and would be
    pushed as the story of the day.
    """
    return (
        StoryCluster.distinct_source_count >= settings.FEED_MIN_DISTINCT_SOURCES,
        listing_age_anchor() >= utc_now() - LISTING_MAX_AGE,
    )
