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
from app.config import settings
from app.models import StoryCluster


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
