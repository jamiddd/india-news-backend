"""Schedule-window and priority ordering for the top-of-app announcement
banner — app/services/announcements.py, used by GET /announcements/active.
No DB needed: the logic under test is pure, given plain objects with
starts_at/ends_at/priority/id.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from app.services.announcements import is_active, sort_active

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)


@dataclass
class Item:
    id: int
    starts_at: datetime
    ends_at: datetime
    priority: int = 0


def at(offset_minutes: int) -> datetime:
    return NOW + timedelta(minutes=offset_minutes)


class TestIsActive:
    def test_within_window_is_active(self):
        assert is_active(Item(1, at(-10), at(10)), NOW)

    def test_not_started_yet_is_inactive(self):
        assert not is_active(Item(1, at(10), at(20)), NOW)

    def test_expired_is_inactive(self):
        assert not is_active(Item(1, at(-20), at(-10)), NOW)

    def test_starts_at_is_inclusive(self):
        assert is_active(Item(1, NOW, at(10)), NOW)

    def test_ends_at_is_exclusive(self):
        assert not is_active(Item(1, at(-10), NOW), NOW)


class TestSortActive:
    def test_drops_upcoming_and_expired(self):
        items = [
            Item(1, at(-10), at(10)),   # active
            Item(2, at(10), at(20)),    # upcoming
            Item(3, at(-20), at(-10)),  # expired
        ]
        assert [i.id for i in sort_active(items, NOW)] == [1]

    def test_orders_by_priority_descending(self):
        items = [
            Item(1, at(-10), at(10), priority=0),
            Item(2, at(-10), at(10), priority=5),
            Item(3, at(-10), at(10), priority=2),
        ]
        assert [i.id for i in sort_active(items, NOW)] == [2, 3, 1]

    def test_ties_break_by_id_ascending(self):
        items = [
            Item(3, at(-10), at(10), priority=1),
            Item(1, at(-10), at(10), priority=1),
            Item(2, at(-10), at(10), priority=1),
        ]
        assert [i.id for i in sort_active(items, NOW)] == [1, 2, 3]

    def test_empty_input(self):
        assert sort_active([], NOW) == []
