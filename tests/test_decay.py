"""
Pure-logic tests for app/services/decay.py's normalized EMA — shared by
piece 1's entity_stats and piece 2's user_entity_affinity. No DB/network.
"""
from datetime import timedelta

from app.services.decay import ema_update


class TestEmaUpdate:
    def test_zero_elapsed_ignores_new_input(self):
        # No time has passed since the last update — nothing to blend in yet.
        assert ema_update(5.0, timedelta(0), timedelta(days=3), 100.0) == 5.0

    def test_first_ever_observation_returns_new_input(self):
        # A huge elapsed (see poller.py/affinity.py's "row is None" case)
        # should fully discount the old (zero) value.
        result = ema_update(0.0, timedelta(days=9999), timedelta(days=3), 4.0)
        assert abs(result - 4.0) < 1e-9

    def test_steady_input_converges_to_itself_regardless_of_half_life(self):
        # The normalization property the design memory depends on: repeatedly
        # feeding the same input at a fixed interval must converge to that
        # same input value, whether the half-life is short or long — this is
        # what makes a fast/slow half-life pair's ratio meaningful.
        for half_life_days in (3, 75):
            value = 0.0
            for _ in range(2000):
                value = ema_update(value, timedelta(days=1), timedelta(days=half_life_days), 2.0)
            assert abs(value - 2.0) < 1e-3

    def test_long_half_life_reacts_slower_than_short(self):
        # After one silent period (input=0) following a steady history, the
        # short half-life should have decayed further toward zero than the
        # long half-life — this lag is the whole reactivation-ratio signal.
        short = ema_update(1.0, timedelta(days=10), timedelta(days=3), 0.0)
        long = ema_update(1.0, timedelta(days=10), timedelta(days=75), 0.0)
        assert short < long
