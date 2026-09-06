"""READ-ONLY comparison of "All Stories" ranking formulas against live data.

Exists because the score has now been wrong in both directions inside a single
day, and neither miss was visible until it reached production. Anchoring decay
on last_updated_at let a 60-source story pin itself to the top for 36 hours;
fixing that while also compressing the numerator to LN(1 + n) overshot the other
way, and the feed filled with two-source stories minutes old. Both changes were
individually defensible and were judged by eye.

This prints what the top of the feed WOULD look like under a set of candidate
formulas, side by side with the one currently deployed, plus the
summary statistics that describe the failure directly: how corroborated the top
of the feed is, and how old. A formula that puts the median source count in the
top 10 at 2 is the regression, whatever its exponents look like.

Writes nothing. Every statement it issues is a SELECT; safe to run against
production.

Usage:
    docker compose -f docker-compose.prod.yml run --rm app \
        python scripts/eval_ranking.py
    ... --at "2026-09-05 14:00"      # rank as of a past moment
    ... --formula "steep:1.0:1.0"    # add a candidate to the comparison
    ... --min-sources 2              # preview the feed with the gate on
    ... --top 30
"""
import argparse
import asyncio
import os
import statistics
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import StoryCluster
from app.services.feed_gate import LISTING_MAX_AGE, gate_min_sources
from app.services.ranking import headline_score


def power_law(breadth: float, decay: float):
    return lambda sources, age_hours: sources ** breadth / (max(age_hours, 0.0) + 2) ** decay


# The formula in production right now, always the left-hand column so every run
# says what it is being compared against. Imported rather than restated: if
# ranking.py changes, this column follows it, and the comparison cannot quietly
# start measuring against a formula that is no longer deployed.
CURRENT = ("deployed", headline_score)

# Candidates worth looking at by default. Chosen to bracket the decision rather
# than to flatter one answer: 0.6/1.2 is barely better than what is deployed,
# 1.0/1.0 approaches the linear counting that caused the original pinning.
DEFAULT_CANDIDATES = [
    ("n^0.6/(h+2)^1.2", power_law(0.6, 1.2)),
    ("n^0.8/(h+2)^1.0", power_law(0.8, 1.0)),
    ("n^1.0/(h+2)^1.0", power_law(1.0, 1.0)),
]


class Row:
    """One cluster, reduced to what ranking actually reads."""

    __slots__ = ("id", "headline", "sources", "age_hours")

    def __init__(self, id, headline, sources, age_hours):
        self.id = id
        self.headline = headline
        self.sources = sources
        self.age_hours = age_hours


def summarise(ranked: list[Row], top: int) -> str:
    """The numbers that describe burial, for the top `top` rows."""
    head = ranked[:top]
    if not head:
        return "(no rows)"
    sources = sorted(c.sources for c in head)
    ages = sorted(c.age_hours for c in head)
    p90 = sources[min(int(len(sources) * 0.9), len(sources) - 1)]
    under_1h = sum(1 for c in head if c.age_hours < 1) / len(head)
    return (f"median {statistics.median(sources):>4.1f} sources  "
            f"p90 {p90:>3} sources  "
            f"median age {statistics.median(ages):>5.1f}h  "
            f"{under_1h:>4.0%} under 1h")


async def load(session, as_of: datetime, min_sources: int) -> list[Row]:
    """Every cluster the feed could show at `as_of`, with the same age anchor
    and the same window the live listing query applies.

    listing_age_anchor() is SQL and this is arithmetic in Python, so the anchor
    is reproduced here by COALESCE-ing the two columns in the same order. They
    are both write-once, which is the property the whole fix rests on.
    """
    result = await session.execute(
        select(
            StoryCluster.id,
            StoryCluster.headline,
            StoryCluster.distinct_source_count,
            StoryCluster.became_multi_source_at,
            StoryCluster.first_seen_at,
        ).where(StoryCluster.distinct_source_count >= min_sources)
    )

    rows = []
    for cid, headline, sources, became_multi, first_seen in result.all():
        anchor = became_multi or first_seen
        if anchor is None:
            continue
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=timezone.utc)
        age = (as_of - anchor).total_seconds() / 3600.0
        # Not yet published as of `as_of`, or past the listing window: invisible
        # either way, so it must not appear in a comparison of what leads.
        if age < 0 or age > LISTING_MAX_AGE.total_seconds() / 3600.0:
            continue
        rows.append(Row(cid, headline or "", sources or 1, age))
    return rows


def print_comparison(rows: list[Row], formulas, top: int, min_sources: int) -> None:
    ranked = {
        name: sorted(rows, key=lambda c, f=fn: f(c.sources, c.age_hours), reverse=True)
        for name, fn in formulas
    }

    print(f"\n{len(rows)} clusters in the window "
          f"(gate >= {min_sources} sources, <= {LISTING_MAX_AGE.days}d old)\n")

    for name, _fn in formulas:
        print(f"  {name:<34} {summarise(ranked[name], top)}")

    baseline = ranked[formulas[0][0]]
    for name, _fn in formulas[1:]:
        print(f"\n{'=' * 100}\n  {formulas[0][0]}   vs   {name}\n{'=' * 100}")
        other = ranked[name]
        for i in range(top):
            left = baseline[i] if i < len(baseline) else None
            right = other[i] if i < len(other) else None
            print(f"{i + 1:>3}. {fmt(left):<48} | {fmt(right)}")

        # The stories the change actually rescues or drops are the point of the
        # exercise; the ordering above is easy to skim past.
        moved_in = [c for c in other[:top] if c not in baseline[:top]]
        moved_out = [c for c in baseline[:top] if c not in other[:top]]
        print(f"\n  RESCUED into the top {top} ({len(moved_in)}):")
        for c in moved_in[:10]:
            print(f"    {fmt(c)}")
        print(f"\n  DROPPED out of the top {top} ({len(moved_out)}):")
        for c in moved_out[:10]:
            print(f"    {fmt(c)}")


def fmt(row: Row | None) -> str:
    if row is None:
        return ""
    return f"{row.sources:>3}src {row.age_hours:>5.1f}h  {row.headline[:32]}"


def parse_formula(raw: str):
    try:
        name, breadth, decay = raw.split(":")
        return (name, power_law(float(breadth), float(decay)))
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"--formula wants name:breadth:decay, e.g. 'steep:1.0:1.0' (got {raw!r})")


async def main(as_of: datetime, formulas, top: int, min_sources: int | None) -> None:
    live_gate = gate_min_sources()
    async with AsyncSessionLocal() as session:
        # Always load ungated, so "what does the gate cost" can be answered from
        # the same query rather than a second trip.
        rows = await load(session, as_of, 1)
    if not rows:
        print("No clusters in the window — nothing to compare.")
        return

    print(f"\nRanking as of {as_of:%Y-%m-%d %H:%M} UTC")

    # The number the gate decision actually turns on: not how many clusters
    # exist, but how much of the recent feed survives it. A gate that halves a
    # four-day archive but empties the last six hours is a gate that shows a
    # reader an empty app.
    print("\n--- what the feed gate costs ---")
    for window in (6, 24, 96):
        recent = [c for c in rows if c.age_hours <= window]
        for n in (2, 3):
            kept = [c for c in recent if c.sources >= n]
            print(f"  last {window:>2}h: {len(recent):>5} clusters -> "
                  f"{len(kept):>5} at >= {n} sources ({len(kept) / max(len(recent), 1):>5.1%})")

    effective = min_sources if min_sources is not None else live_gate
    if min_sources is not None and min_sources != live_gate:
        print(f"\n(previewing gate >= {min_sources}; live setting is >= {live_gate})")
    rows = [c for c in rows if c.sources >= effective]
    print_comparison(rows, formulas, top, effective)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--at", type=str, default=None,
                    help="Rank as of this UTC moment (YYYY-MM-DD HH:MM), not now. "
                         "Use it to replay a time whose correct answer you remember.")
    ap.add_argument("--formula", type=parse_formula, action="append", default=None,
                    help="name:breadth:decay — repeatable. Replaces the defaults.")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--min-sources", type=int, default=None,
                    help="Preview the feed at this gate threshold instead of the "
                         "live FEED_GATE_ENABLED/FEED_MIN_DISTINCT_SOURCES setting.")
    args = ap.parse_args()

    if args.at:
        moment = datetime.strptime(args.at, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    else:
        moment = datetime.now(timezone.utc)

    asyncio.run(main(moment, [CURRENT] + (args.formula or DEFAULT_CANDIDATES),
                     args.top, args.min_sources))
