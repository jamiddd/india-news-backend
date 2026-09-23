"""Pure scheduling/ordering logic for the top-of-app announcement banner —
see app/models.py's Announcement and GET /announcements/active in
app/main.py. Split out from the endpoint so the schedule-window and
priority-ordering rules can be unit tested without a database.
"""
from __future__ import annotations

from datetime import datetime
from typing import Protocol


class ScheduledItem(Protocol):
    id: int
    starts_at: datetime
    ends_at: datetime
    priority: int


def is_active(item: ScheduledItem, now: datetime) -> bool:
    """An announcement is live from `starts_at` (inclusive) up to but not
    including `ends_at`, so it disappears the instant its window closes."""
    return item.starts_at <= now < item.ends_at


def sort_active(items: list[ScheduledItem], now: datetime) -> list[ScheduledItem]:
    """Active items only, highest `priority` first; ties broken by `id` so
    ordering is stable (matches GET /topics/active's own tie-break)."""
    active = [item for item in items if is_active(item, now)]
    return sorted(active, key=lambda item: (-item.priority, item.id))
