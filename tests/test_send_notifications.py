"""
Pure-logic tests for the daily-digest time-window matching in
scripts/send_notifications.py. No DB/network/Firebase call involved — these
just exercise the window math, including the midnight-wrap edge case.
"""
from datetime import datetime, timezone

from scripts.send_notifications import (
    _minutes_since_midnight,
    _is_within_daily_window,
    DAILY_WINDOW_MINUTES,
)


class TestMinutesSinceMidnight:
    def test_parses_hh_mm(self):
        assert _minutes_since_midnight("09:30") == 9 * 60 + 30

    def test_midnight_is_zero(self):
        assert _minutes_since_midnight("00:00") == 0


class TestIsWithinDailyWindow:
    def test_exact_match(self):
        now = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)
        assert _is_within_daily_window("09:00", now) is True

    def test_within_window_minutes(self):
        now = datetime(2026, 1, 1, 9, DAILY_WINDOW_MINUTES, tzinfo=timezone.utc)
        assert _is_within_daily_window("09:00", now) is True

    def test_outside_window_minutes(self):
        now = datetime(2026, 1, 1, 9, DAILY_WINDOW_MINUTES + 1, tzinfo=timezone.utc)
        assert _is_within_daily_window("09:00", now) is False

    def test_far_from_target_is_not_eligible(self):
        now = datetime(2026, 1, 1, 15, 0, tzinfo=timezone.utc)
        assert _is_within_daily_window("09:00", now) is False

    def test_midnight_wrap_forward(self):
        # preferred time 23:58, now 00:02 the next day — only 4 minutes
        # apart across the wrap, should still be eligible.
        now = datetime(2026, 1, 1, 0, 2, tzinfo=timezone.utc)
        assert _is_within_daily_window("23:58", now) is True

    def test_midnight_wrap_backward(self):
        # preferred time 00:02, now 23:58 the previous day.
        now = datetime(2026, 1, 1, 23, 58, tzinfo=timezone.utc)
        assert _is_within_daily_window("00:02", now) is True

    def test_not_near_midnight_at_all(self):
        now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        assert _is_within_daily_window("00:00", now) is False
