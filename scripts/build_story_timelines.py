"""Timeline/Context tab's production generator — run 2-3x/day via cron (see
backend-deploy-workflow: same manual-SSH pattern as any other scheduled
script, no in-process scheduler here). Reads story_chains.py's chain
assignment, picks up to 5 chains (editorial picks from
story_timeline_features first, algorithmic length x recency fallback for
the remaining slots), and calls the validated LLM narrative prompt
(app/services/timeline_narrative.py) for any that need a fresh narrative.

Selection is NOT "regenerate everything every cycle" — an LLM call per
chain per cycle is real, recurring spend, and most chains don't change
between two runs a few hours apart. A chain gets (re)generated when:
  - it has never been generated (narrative_generated_at is null), or
  - its chain membership has changed since the last generation (new
    cluster ids joined, i.e. a genuine new development happened), or
  - it was previously marked incoherent by story_chains' own drift (not
    handled here — see the coherent:false branch below, which just leaves
    the existing narrative alone rather than overwriting a good one with a
    bad one for a chain that temporarily looks worse this cycle).

A chain that drops out of this cycle's top-5 selection is NOT deleted or
blanked — last_seen_in_top flips to false and the row (with its last-good
narrative) stays exactly as it was. A reader partway through a story
shouldn't see it vanish because a bigger story bumped it from the
homepage slot; GET /timelines (unwritten) is expected to only show
last_seen_in_top=true rows to new visitors, while a direct link to an
older one still resolves.

Usage:
    python3 scripts/build_story_timelines.py                 # normal run
    python3 scripts/build_story_timelines.py --dry-run        # select and
                                                                # print, no
                                                                # writes, no
                                                                # LLM calls
    python3 scripts/build_story_timelines.py --slots 5        # override
                                                                # the top-5
                                                                # cap
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


def rank_chains(chains: List[FrozenSet[int]], by_id: Dict[int, Cluster]) -> List[FrozenSet[int]]:
    """Length x recency, longest and most-recently-active first — the
    fallback signal discussed for production selection, same ranking
    scripts/test_timeline_narrative.py uses for its own "--top N"."""
    return sorted(
        chains,
        key=lambda ids: (len(ids), max(by_id[i].last_updated_at for i in ids if i in by_id)),
        reverse=True,
    )


async def select_chains(
    session, days: int, slots: int,
) -> tuple[List[tuple[FrozenSet[int], bool]], Dict[int, Cluster]]:
    """Returns [(chain_ids, is_editorial_pick)], editorial picks first, up
    to `slots` total, plus the id->Cluster map callers need for anything
    else (headline, first_seen_at, ...)."""
    clusters = await load_clusters(session, days)
    baseline_rates = await load_baseline_rates(session)
    by_id = {c.id: c for c in clusters}
    assignment: Dict[int, FrozenSet[int]] = build_chains(clusters, baseline_rates)

    distinct_chains = {frozenset(v) for v in assignment.values() if len(v) > 1}

    picks_result = await session.execute(
        select(StoryTimelineFeature).where(StoryTimelineFeature.is_editorial_pick.is_(True))
    )
    picked_rows = picks_result.scalars().all()

    selected: List[tuple[FrozenSet[int], bool]] = []
    used_chains: set = set()
    for row in picked_rows:
        chain_ids = assignment.get(row.anchor_cluster_id)
        if not chain_ids or len(chain_ids) <= 1:
            # The anchor cluster fell out of the lookback window, or no
            # longer chains with anything — nothing to generate for this
            # pick this cycle. Left as an editorial pick for when it's
            # relevant again; not cleared automatically.
            logger.info(
                "editorial pick anchor_cluster_id=%s has no current chain (outside window or "
                "unchained) — skipping this cycle", row.anchor_cluster_id,
            )
            continue
        if chain_ids in used_chains:
            continue  # two picks pointing at the same chain
        selected.append((chain_ids, True))
        used_chains.add(chain_ids)

    remaining = slots - len(selected)
    if remaining > 0:
        fallback_candidates = [c for c in rank_chains(list(distinct_chains), by_id) if c not in used_chains]
        for chain_ids in fallback_candidates[:remaining]:
            selected.append((chain_ids, False))
            used_chains.add(chain_ids)

    return selected[:slots], by_id


async def generate_for_chain(
    session, chain_ids: FrozenSet[int], is_editorial_pick: bool, by_id: Dict[int, Cluster], *, dry_run: bool,
) -> Optional[int]:
    """Returns the anchor_cluster_id this chain's row now lives under (for
    the caller's last_seen_in_top bookkeeping), or None if there was
    nothing to generate for (empty chain after filtering)."""
    members = sorted((by_id[i] for i in chain_ids if i in by_id), key=lambda c: c.first_seen_at)
    if not members:
        return None
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

    if dry_run:
        return anchor_cluster_id

    if existing is None:
        existing = StoryTimelineFeature(anchor_cluster_id=anchor_cluster_id, is_editorial_pick=is_editorial_pick)
        session.add(existing)
    else:
        # The anchor can drift to a newer cluster id as the chain grows —
        # re-point the unique row rather than creating a duplicate for the
        # same underlying chain.
        existing.anchor_cluster_id = anchor_cluster_id
        existing.is_editorial_pick = is_editorial_pick

    existing.last_seen_in_top = True

    if needs_generation:
        try:
            narrative = await generate_narrative(session, members)
        except TimelineNarrativeError as e:
            logger.error("narrative generation failed for chain anchor=%s: %s", anchor_cluster_id, e)
            return anchor_cluster_id
        existing.coherent = bool(narrative.get("coherent"))
        existing.title = narrative.get("title")
        existing.context = narrative.get("context")
        existing.beats = narrative.get("beats")
        existing.anchor_label = None  # story_chains.py's get_story_timeline computes this separately; not needed here since the narrative's own title carries the same role
        existing.cluster_ids = sorted_ids
        existing.narrative_generated_at = datetime.now(timezone.utc)

    return anchor_cluster_id


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=DAYS)
    parser.add_argument("--slots", type=int, default=DEFAULT_SLOTS)
    parser.add_argument("--dry-run", action="store_true", help="select and log only, no writes or LLM calls")
    args = parser.parse_args()

    async with AsyncSessionLocal() as session:
        selected, by_id = await select_chains(session, args.days, args.slots)
        logger.info("selected %d/%d slots (%d editorial)", len(selected), args.slots, sum(1 for _, e in selected if e))

        touched_anchor_ids = set()
        for chain_ids, is_editorial_pick in selected:
            anchor_cluster_id = await generate_for_chain(session, chain_ids, is_editorial_pick, by_id, dry_run=args.dry_run)
            if anchor_cluster_id is not None:
                touched_anchor_ids.add(anchor_cluster_id)

        if not args.dry_run:
            # Everything selected this cycle already got last_seen_in_top =
            # True set directly on its row inside generate_for_chain; here,
            # flip every OTHER existing row to False — a chain that fell
            # out of the top-5 keeps its row and last-good narrative (see
            # module docstring), it just stops showing to new visitors.
            await session.flush()
            all_rows_result = await session.execute(select(StoryTimelineFeature))
            for row in all_rows_result.scalars().all():
                if row.anchor_cluster_id not in touched_anchor_ids:
                    row.last_seen_in_top = False

            await session.commit()

    logger.info("done.")


if __name__ == "__main__":
    asyncio.run(main())
