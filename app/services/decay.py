"""
Shared normalized-EMA decay math for the feed ranking redesign. Used by
piece 1's entity_stats (app/services/poller.py) and piece 2's
user_entity_affinity (app/services/affinity.py) — same formula, different
half-lives, so it lives in one place instead of being duplicated.

See the "Feed ranking redesign" design memory for why this must be the
normalized form (rate_new = rate_old * decay + input * (1 - decay)) rather
than a raw decayed sum: normalization keeps values on the same "expected
input per update" scale regardless of half-life, so a steady input rate
always converges to itself no matter which half-life is used to track it —
only how fast it *reacts* to a change in rate differs. That's what makes a
fast/slow half-life pair comparable as a ratio (piece 1) or usable as a
drifting personal-interest signal (piece 2).
"""
from datetime import timedelta


def ema_update(old_value: float, elapsed: timedelta, half_life: timedelta, new_input: float) -> float:
    if elapsed.total_seconds() <= 0:
        decay = 1.0
    else:
        decay = 0.5 ** (elapsed.total_seconds() / half_life.total_seconds())
    return old_value * decay + new_input * (1.0 - decay)
