"""Timeline/Context tab's production generator — run 2-3x/day via cron (see
backend-deploy-workflow: same manual-SSH pattern as any other scheduled
script, no in-process scheduler here). Reads story_chains.py's chain
assignment and fills up to 5 slots: editorial picks from
story_timeline_features first, then an algorithmic length x recency
fallback scan for the rest — calling the validated LLM narrative prompt
(app/services/timeline_narrative.py) as needed.

A fallback candidate that comes back coherent:false does NOT fill a slot —
see the 2026-09-07 live run where 3 of 5 algorithmically-picked chains
were person/topic-blob false positives (a Musk-mentions grab-bag, a UN
General Assembly session grab-bag, an Assam-geography grab-bag), all
correctly caught by the coherence prompt but written with
last_seen_in_top=true regardless in the first version of this script —
which would have shown blob narratives with an empty title to real users,
since GET /timelines' only signal for "should this show" was
last_seen_in_top. Fixed: the fallback scan keeps trying the next-ranked
candidate until `slots` worth of COHERENT narratives are found or the
scan cap is hit, and only coherent (or editorial) rows get
last_seen_in_top=true. Editorial picks always occupy their slot
regardless of coherence outcome — that's the admin's explicit call, not
something this script should second-guess by swapping in a fallback
instead.

Selection is NOT "regenerate everything every cycle" — an LLM call per
chain per cycle is real, recurring spend, and most chains don't change
between two runs a few hours apart. A chain gets (re)generated when it's
never been generated, or its membership has changed since the last
generation (a genuine new development happened). An unchanged chain keeps
its last stored coherent verdict rather than re-asking the LLM.

A chain that drops out of this cycle's selection entirely (not reached by
the scan, or superseded) is NOT deleted or blanked — last_seen_in_top
flips to false and the row (with its last-good narrative, if it had one)
stays exactly as it was. A reader partway through a story shouldn't see
it vanish because a bigger story bumped it from the homepage slot;
GET /timelines (unwritten) is expected to only show last_seen_in_top=true
rows to new visitors, while a direct link to an older one still resolves.

Usage:
    python3 scripts/build_story_timelines.py                 # normal run
    python3 scripts/build_story_timelines.py --dry-run        # select and
                                                                # print, no
                                                                # writes —
                                                                # STILL
                                                                # calls the
                                                                # LLM for
                                                                # any chain
                                                                # needing
                                                                # generation,
                                                                # since a
                                                                # dry run
                                                                # can't
                                                                # otherwise
                                                                # know
                                                                # whether a
                                                                # new
                                                                # candidate
                                                                # would be
                                                                # coherent
    python3 scripts/build_story_timelines.py --slots 5        # override
                                                                # the top-5
                                                                # cap
    python3 scripts/build_story_timelines.py --scan-cap 20    # override
                                                                # how many
                                                                # fallback
                                                                # candidates
                                                                # to try
                                                                # before
                                                                # giving up
                                                                # on filling
                                                                # every slot
"""
import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Dict, FrozenSet, List, Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select  # noqa: E402

from app.database import AsyncSessionLocal  # noqa: E402
from app.models import StoryTimelineFeature  # noqa: E402
from app.services.story_chains import (  # noqa: E402
    DAYS,
    Cluster,
    build_chains,
    load_baseline_rates,
    load_clusters,
)
from app.services.timeline_narrative import TimelineNarrativeError, generate_narrative  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DEFAULT_SLOTS = 5
# How many fallback candidates to try, at most, before giving up on filling
# every slot this cycle — bounds LLM spend against a bad run of blobs
# instead of scanning all 474+ distinct chains looking for 5 good ones.
DEFAULT_SCAN_CAP_MULTIPLIER = 4


def rank_chains(chains: List[FrozenSet[int]], by_id: Dict[int, Cluster]) -> List[FrozenSet[int]]:
    """Length x recency, longest and most-recently-active first — the
    fallback signal discussed for production selection, same ranking
    scripts/test_timeline_narrative.py uses for its own "--top N"."""
    return sorted(
        chains,
        key=lambda ids: (len(ids), max(by_id[i].last_updated_at for i in ids if i in by_id)),
        reverse=True,
    )


async def load_candidates(
    session, days: int,
) -> tuple[Dict[int, Cluster], Dict[int, FrozenSet[int]], set, List[StoryTimelineFeature]]:
    """Shared setup: clusters, the chain assignment, the set of cluster ids
    belonging to already-rejected chains (see the overlap note below), and
    the current editorial-pick rows."""
    clusters = await load_clusters(session, days)
    baseline_rates = await load_baseline_rates(session)
    by_id = {c.id: c for c in clusters}
    assignment: Dict[int, FrozenSet[int]] = build_chains(clusters, baseline_rates)

    rejected_result = await session.execute(
        select(StoryTimelineFeature.cluster_ids).where(StoryTimelineFeature.coherent.is_(False))
    )
    # Overlap, not exact-set equality — a rejected chain's live membership
    # drifts run to run just like any other (grows, or shrinks as clusters
    # age out of the lookback window), so an exact-match check stops
    # excluding it almost immediately. Any shared member means "this is
    # still substantially the same blob", which is the thing that was
    # actually rejected.
    known_incoherent_ids: set = set()
    for (ids,) in rejected_result.all():
        if ids:
            known_incoherent_ids.update(ids)

    picks_result = await session.execute(
        select(StoryTimelineFeature).where(StoryTimelineFeature.is_editorial_pick.is_(True))
    )
    picked_rows = picks_result.scalars().all()

    return by_id, assignment, known_incoherent_ids, picked_rows


async def generate_for_chain(
    session, chain_ids: FrozenSet[int], is_editorial_pick: bool, by_id: Dict[int, Cluster], *, dry_run: bool,
) -> tuple[Optional[int], Optional[bool]]:
    """Returns (anchor_cluster_id, coherent) — coherent is None when nothing
    could be determined this cycle (empty chain after filtering, or
    generation failed and there's no prior stored verdict to fall back on).
    Does NOT set last_seen_in_top; the caller decides that once it knows
    whether this chain actually fills a slot.
    """
    members = sorted((by_id[i] for i in chain_ids if i in by_id), key=lambda c: c.first_seen_at)
    if not members:
        return None, None
    anchor_cluster_id = members[-1].id  # most recent member — a fresh anchor each cycle as the chain grows
    sorted_ids = sorted(chain_ids)

    existing_result = await session.execute(
        select(StoryTimelineFeature).where(StoryTimelineFeature.anchor_cluster_id.in_(list(chain_ids)))
    )
    existing = existing_result.scalars().first()

    needs_generation = (
        existing is None
        or existing.narrative_generated_at is None
        or (existing.cluster_ids or []) != sorted_ids
    )

    logger.info(
        "chain of %d (anchor=%s, editorial=%s): %s",
        len(members), anchor_cluster_id, is_editorial_pick,
        "generating" if needs_generation else "unchanged since last generation, skipping LLM call",
    )

    if not needs_generation:
        return anchor_cluster_id, existing.coherent

    if dry_run:
        # A dry run can't know whether a genuinely new candidate would be
        # coherent without actually calling the LLM — that's the one case
        # where --dry-run still spends a call, since "select only" would
        # otherwise be unable to report anything useful for new chains.
        try:
            narrative = await generate_narrative(session, members)
            return anchor_cluster_id, bool(narrative.get("coherent"))
        except TimelineNarrativeError as e:
            logger.error("dry-run narrative generation failed for chain anchor=%s: %s", anchor_cluster_id, e)
            return anchor_cluster_id, None

    if existing is None:
        existing = StoryTimelineFeature(anchor_cluster_id=anchor_cluster_id, is_editorial_pick=is_editorial_pick)
        session.add(existing)
    else:
        # The anchor can drift to a newer cluster id as the chain grows —
        # re-point the unique row rather than creating a duplicate for the
        # same underlying chain.
        existing.anchor_cluster_id = anchor_cluster_id
        existing.is_editorial_pick = is_editorial_pick

    try:
        narrative = await generate_narrative(session, members)
    except TimelineNarrativeError as e:
        logger.error("narrative generation failed for chain anchor=%s: %s", anchor_cluster_id, e)
        return anchor_cluster_id, None

    existing.coherent = bool(narrative.get("coherent"))
    existing.title = narrative.get("title")
    existing.context = narrative.get("context")
    existing.beats = narrative.get("beats")
    existing.anchor_label = None  # story_chains.py's get_story_timeline computes this separately; not needed here since the narrative's own title carries the same role
    existing.cluster_ids = sorted_ids
    existing.narrative_generated_at = datetime.now(timezone.utc)

    return anchor_cluster_id, existing.coherent


async def set_last_seen_in_top(session, anchor_cluster_id: int, value: bool, *, dry_run: bool) -> None:
    if dry_run or anchor_cluster_id is None:
        return
    result = await session.execute(
        select(StoryTimelineFeature).where(StoryTimelineFeature.anchor_cluster_id == anchor_cluster_id)
    )
    row = result.scalar_one_or_none()
    if row is not None:
        row.last_seen_in_top = value


async def run(*, days: int = DAYS, slots: int = DEFAULT_SLOTS, scan_cap: Optional[int] = None, dry_run: bool = False) -> None:
    """The actual generation cycle — called by both this script's CLI
    (`main`, below) and scripts/run_timeline_scheduler.py's daemon loop, so
    the two never drift into re-implementing selection separately."""
    scan_cap = scan_cap or slots * DEFAULT_SCAN_CAP_MULTIPLIER

    async with AsyncSessionLocal() as session:
        by_id, assignment, known_incoherent_ids, picked_rows = await load_candidates(session, days)
        distinct_chains = {frozenset(v) for v in assignment.values() if len(v) > 1}
        distinct_chains = {c for c in distinct_chains if not (c & known_incoherent_ids)}

        used_cluster_ids: set = set()
        filled_anchor_ids: set = set()  # rows that should end this cycle with last_seen_in_top=True
        attempted_anchor_ids: set = set()  # every row touched this cycle, filled or not

        # Editorial picks: always attempted, always occupy a slot regardless
        # of this cycle's coherence outcome — see module docstring. story_
        # chains.py's assignment isn't a strict partition (see the overlap
        # comment in generate_for_chain's caller below), so also dedupe
        # editorial picks against each other by member overlap.
        editorial_count = 0
        for row in picked_rows:
            chain_ids = assignment.get(row.anchor_cluster_id)
            if not chain_ids or len(chain_ids) <= 1:
                logger.info(
                    "editorial pick anchor_cluster_id=%s has no current chain (outside window or "
                    "unchained) — skipping this cycle", row.anchor_cluster_id,
                )
                continue
            if chain_ids & used_cluster_ids:
                continue
            used_cluster_ids |= chain_ids
            editorial_count += 1
            anchor_cluster_id, _coherent = await generate_for_chain(session, chain_ids, True, by_id, dry_run=dry_run)
            if anchor_cluster_id is not None:
                attempted_anchor_ids.add(anchor_cluster_id)
                filled_anchor_ids.add(anchor_cluster_id)

        # Fallback: scan ranked candidates (excluding anything already used
        # by an editorial pick or known-incoherent), only counting a
        # candidate as filling a slot when it comes back coherent. A
        # candidate that overlaps one already touched this cycle — even a
        # rejected one — is skipped rather than re-attempted, since
        # story_chains.py's non-strict-partition quirk means an "overlap"
        # here means "substantially the same real-world story", not a
        # coincidence.
        remaining = slots - editorial_count
        scanned = 0
        if remaining > 0:
            for chain_ids in rank_chains(list(distinct_chains), by_id):
                if remaining <= 0 or scanned >= scan_cap:
                    break
                if chain_ids & used_cluster_ids:
                    continue
                scanned += 1
                used_cluster_ids |= chain_ids
                anchor_cluster_id, coherent = await generate_for_chain(session, chain_ids, False, by_id, dry_run=dry_run)
                if anchor_cluster_id is None:
                    continue
                attempted_anchor_ids.add(anchor_cluster_id)
                if coherent:
                    filled_anchor_ids.add(anchor_cluster_id)
                    remaining -= 1
                else:
                    logger.info("chain anchor=%s rejected (coherent=%s) — does not fill a slot, trying next candidate", anchor_cluster_id, coherent)

        logger.info(
            "filled %d/%d slots (%d editorial, %d algorithmic; scanned %d fallback candidates)",
            len(filled_anchor_ids), slots, editorial_count, len(filled_anchor_ids) - editorial_count, scanned,
        )
        if remaining > 0:
            logger.warning("%d slot(s) left unfilled this cycle — ran out of coherent candidates within the scan cap", remaining)

        if not dry_run:
            for anchor_cluster_id in attempted_anchor_ids:
                await set_last_seen_in_top(session, anchor_cluster_id, anchor_cluster_id in filled_anchor_ids, dry_run=False)

            # Everything else (not touched this cycle at all) falls out of
            # the tab's top view without losing its narrative — see module
            # docstring.
            await session.flush()
            all_rows_result = await session.execute(select(StoryTimelineFeature))
            for row in all_rows_result.scalars().all():
                if row.anchor_cluster_id not in attempted_anchor_ids:
                    row.last_seen_in_top = False

            await session.commit()

    logger.info("done.")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=DAYS)
    parser.add_argument("--slots", type=int, default=DEFAULT_SLOTS)
    parser.add_argument("--scan-cap", type=int, default=None, help=f"max fallback candidates to try (default: slots x {DEFAULT_SCAN_CAP_MULTIPLIER})")
    parser.add_argument("--dry-run", action="store_true", help="select and log only; still calls the LLM for any new/changed candidate (see module docstring), just skips all writes")
    args = parser.parse_args()
    await run(days=args.days, slots=args.slots, scan_cap=args.scan_cap, dry_run=args.dry_run)


if __name__ == "__main__":
    asyncio.run(main())
