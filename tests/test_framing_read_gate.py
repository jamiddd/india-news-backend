"""
Read-path age-gating for framing comparisons (app.main._framing_for_response).

This is the other half of the fix for the framing-blanking window. The write
path no longer nulls framing_comparison on every refinement (see
app.services.enrichment.apply_baseline_enrichment), so bounding staleness moved
here — which also covers the case the old clearing could not distinguish from a
batch still in flight: one that fails, is cancelled, or never lands.
"""
from datetime import datetime, timedelta, timezone

from app.main import FRAMING_MAX_AGE, _framing_for_response


class _StubCluster:
    def __init__(self, framing, last_enriched_at):
        self.framing_comparison = framing
        self.last_enriched_at = last_enriched_at


FRAMING = [{"outlet": "NDTV", "headline_angle": "Official"}]


def _ago(delta):
    return datetime.now(timezone.utc) - delta


class TestFramingReadGate:
    def test_serves_framing_from_a_recent_pass(self):
        c = _StubCluster(FRAMING, _ago(timedelta(hours=1)))
        assert _framing_for_response(c) == FRAMING

    def test_hides_framing_the_pipeline_has_abandoned(self):
        c = _StubCluster(FRAMING, _ago(FRAMING_MAX_AGE + timedelta(hours=1)))
        assert _framing_for_response(c) is None

    def test_an_in_flight_batch_never_hides_a_live_comparison(self):
        # The gate must sit well past the Batch API's 24h worst case, or a
        # refinement in flight would reintroduce the very blanking this fixes.
        assert FRAMING_MAX_AGE > timedelta(hours=24)
        c = _StubCluster(FRAMING, _ago(timedelta(hours=24)))
        assert _framing_for_response(c) == FRAMING

    def test_serves_framing_backfilled_before_last_enriched_at_existed(self):
        # NULL last_enriched_at means "unknown age", which is the pre-existing
        # situation — dropping it would regress every backfilled row.
        c = _StubCluster(FRAMING, None)
        assert _framing_for_response(c) == FRAMING

    def test_naive_timestamps_are_read_as_utc(self):
        # Postgres can hand back a naive datetime depending on the column and
        # driver; subtracting one from an aware now() raises TypeError, which
        # would 500 the detail endpoint.
        naive = datetime.utcnow() - timedelta(hours=1)
        assert naive.tzinfo is None
        assert _framing_for_response(_StubCluster(FRAMING, naive)) == FRAMING

    def test_absent_framing_stays_absent(self):
        assert _framing_for_response(_StubCluster(None, _ago(timedelta(hours=1)))) is None
        assert _framing_for_response(_StubCluster([], _ago(timedelta(hours=1)))) is None
