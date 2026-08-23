"""
Pure engagement-scoring math for the feed ranking redesign, piece 3
(explore-slot bandit) — no DB/network, kept separate from
app/services/explore_bandit.py (which needs SQLAlchemy for the rest of the
bandit logic) purely so this piece stays testable without a DB, same
pattern as app/services/decay.py and entity_graph.py.
"""
from typing import Optional

# Approximate reading speed used to turn an article's word count into an
# "expected" dwell time.
WORDS_PER_MINUTE = 200


def engagement(dwell_ms: Optional[int], scroll_depth_pct: Optional[int], word_count: int) -> float:
    """dwell relative to expected reading time, times scroll depth — a raw
    click (dwell_ms present but tiny) scores near 0 on both terms; a full
    read scores near 1 on both. No matching read_events row (never opened)
    should be passed as dwell_ms=None by the caller, scoring 0 here."""
    if dwell_ms is None or word_count <= 0:
        return 0.0
    expected_dwell_ms = (word_count / WORDS_PER_MINUTE) * 60_000
    dwell_ratio = min(dwell_ms / expected_dwell_ms, 1.0) if expected_dwell_ms > 0 else 0.0
    scroll_ratio = (scroll_depth_pct or 0) / 100.0
    return dwell_ratio * scroll_ratio
