"""
Pure-logic tests for app/services/engagement.py — the engagement scoring
used by piece 3's explore_bandit.py (pick_candidate/record_exposure/
recompute_explore_promotions need a real DB session and are exercised via
manual server verification, same as pieces 1/2).
"""
from app.services.engagement import engagement as _engagement


class TestEngagement:
    def test_never_opened_scores_zero(self):
        assert _engagement(dwell_ms=None, scroll_depth_pct=None, word_count=300) == 0.0

    def test_bounce_scores_near_zero(self):
        # Opened, closed almost immediately, barely scrolled.
        result = _engagement(dwell_ms=500, scroll_depth_pct=5, word_count=300)
        assert result < 0.05

    def test_full_read_scores_near_one(self):
        # ~300 words at 200wpm ≈ 90s expected; dwell well past that, full scroll.
        result = _engagement(dwell_ms=120_000, scroll_depth_pct=100, word_count=300)
        assert result > 0.9

    def test_dwell_ratio_caps_at_one(self):
        # Dwelling 10x the expected time shouldn't out-score a merely-on-time
        # full read — both are capped at the same max.
        capped = _engagement(dwell_ms=1_200_000, scroll_depth_pct=100, word_count=300)
        on_time = _engagement(dwell_ms=90_000, scroll_depth_pct=100, word_count=300)
        assert abs(capped - on_time) < 1e-9

    def test_partial_scroll_scales_down_even_with_long_dwell(self):
        # Scroll depth still gates the score even if dwell alone looks great
        # (e.g. phone left open) — this is the whole reason scroll depth is
        # a separate multiplicative factor, not folded into dwell alone.
        result = _engagement(dwell_ms=120_000, scroll_depth_pct=20, word_count=300)
        assert result < 0.25

    def test_zero_word_count_does_not_crash(self):
        # Defensive: DEFAULT_WORD_COUNT should prevent this in practice, but
        # the function itself shouldn't divide by zero if it ever sees one.
        result = _engagement(dwell_ms=1000, scroll_depth_pct=50, word_count=0)
        assert result == 0.0
