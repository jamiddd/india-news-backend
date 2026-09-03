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
from app.services.feed_gate import apply_feed_gate, gate_cache_marker, gate_min_sources


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
