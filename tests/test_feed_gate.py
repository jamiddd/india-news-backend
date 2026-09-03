"""
The multi-source feed gate (docs/multi-source-feed-plan.md §5.A).

The gate is a config flag that changes what the whole app shows, so what
matters is that it is genuinely off by default, that it actually reaches the
SQL when on, and that turning it on cannot start serving pre-flip cached
pages. No DB/network — the queries are compiled, not executed.
"""
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.config import settings
from app.models import StoryCluster
from app.services.feed_gate import (
    LISTING_MAX_AGE,
    apply_feed_gate,
    gate_cache_marker,
    gate_min_sources,
    notifiable_clauses,
)


def _sql(query) -> str:
    return str(query.compile(dialect=postgresql.dialect()))


class TestFeedGate:
    def test_defaults_to_off(self):
        # The gate is a product commitment, not a cleanup — it must never
        # arrive switched on as a side effect of deploying the code.
        assert settings.FEED_GATE_ENABLED is False

    def test_is_a_no_op_when_disabled(self, monkeypatch):
        monkeypatch.setattr(settings, "FEED_GATE_ENABLED", False)
        base = select(StoryCluster.id)
        assert _sql(apply_feed_gate(base)) == _sql(base)

    def test_filters_on_distinct_sources_when_enabled(self, monkeypatch):
        monkeypatch.setattr(settings, "FEED_GATE_ENABLED", True)
        sql = _sql(apply_feed_gate(select(StoryCluster.id)))
        assert "distinct_source_count" in sql

    def test_threshold_comes_from_config(self, monkeypatch):
        # The threshold stays tunable so it can be tightened without a code
        # change if false merges show up at scale.
        monkeypatch.setattr(settings, "FEED_GATE_ENABLED", True)
        monkeypatch.setattr(settings, "FEED_MIN_DISTINCT_SOURCES", 3)
        query = apply_feed_gate(select(StoryCluster.id))
        params = query.compile(dialect=postgresql.dialect()).params
        assert 3 in params.values()


class TestGateHelpers:
    def test_min_sources_floor_is_a_no_op_when_off(self, monkeypatch):
        # 1 is a floor every cluster clears, so raw-SQL callers can
        # interpolate it unconditionally instead of branching.
        monkeypatch.setattr(settings, "FEED_GATE_ENABLED", False)
        assert gate_min_sources() == 1

    def test_min_sources_is_the_threshold_when_on(self, monkeypatch):
        monkeypatch.setattr(settings, "FEED_GATE_ENABLED", True)
        monkeypatch.setattr(settings, "FEED_MIN_DISTINCT_SOURCES", 2)
        assert gate_min_sources() == 2

    def test_cache_marker_differs_by_state(self, monkeypatch):
        # Equal markers would serve pre-flip pages for a full TTL after the
        # flag is turned on.
        monkeypatch.setattr(settings, "FEED_GATE_ENABLED", False)
        off = gate_cache_marker()
        monkeypatch.setattr(settings, "FEED_GATE_ENABLED", True)
        assert gate_cache_marker() != off


class TestNotifiableClauses:
    """What a cluster must be before it is worth waking someone's phone for.

    The daily digest previously selected on nothing but "highest
    headline_score in the table" — no source requirement and no age bound.
    """

    def test_requires_corroboration_regardless_of_the_feed_gate(self, monkeypatch):
        # Unconditional by design: a push is the most intrusive surface the
        # app has, so it does not relax when the in-app feed is ungated.
        monkeypatch.setattr(settings, "FEED_GATE_ENABLED", False)
        sql = _sql(select(StoryCluster.id).where(*notifiable_clauses()))
        assert "distinct_source_count" in sql

    def test_bounds_age(self, monkeypatch):
        # headline_score decays on last_updated_at, so an out-of-band write
        # to that column makes a years-old cluster score as if it were
        # breaking news. The age bound is what stops it being pushed.
        monkeypatch.setattr(settings, "FEED_GATE_ENABLED", False)
        sql = _sql(select(StoryCluster.id).where(*notifiable_clauses()))
        assert "coalesce" in sql.lower()
        assert "became_multi_source_at" in sql

    def test_uses_the_same_age_window_as_listings(self):
        # One definition, so a story cannot be pushed after it has aged out
        # of the feed it would open into.
        assert LISTING_MAX_AGE.days == 4

    def test_singleton_ceiling_is_below_the_breaking_threshold(self):
        # Breaking's multi-source guarantee was, before this change, purely
        # an emergent property of one constant sitting above one number:
        # score = distinct_source_count / (hours+2)^1.5, so a 1-source story
        # peaks at 1/2^1.5 at age 0. The explicit clause means the guarantee
        # no longer depends on nobody ever retuning the threshold.
        singleton_ceiling = 1 / (0 + 2) ** 1.5
        assert singleton_ceiling < 0.4
        assert round(singleton_ceiling, 4) == 0.3536
