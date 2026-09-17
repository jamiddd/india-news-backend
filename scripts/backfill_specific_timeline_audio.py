"""One-off, scoped variant of scripts/backfill_all_timeline_audio.py — same
regeneration logic (regenerate spoken_script when style_scene/style_context
is missing, then resynthesize audio), but restricted to an explicit list of
anchor_cluster_ids instead of "missing audio" or "every coherent row".

Built for the 2026-09-17 audit: 6 rows found on the pre-emotion-tag spoken_
script pattern (see scripts/audit_spoken_script_pattern.py) that neither of
backfill_all_timeline_audio.py's two modes cleanly targets — 3 have no
audio at all (would be caught by the default scope), but 3 already have
audio from the old script and would only be touched by --all, which would
also needlessly re-synthesize the ~15 rows that are already current.

Usage:
    python3 scripts/backfill_specific_timeline_audio.py             # regen the 6 below
    python3 scripts/backfill_specific_timeline_audio.py --dry-run   # report only
"""
import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select  # noqa: E402

from app.database import AsyncSessionLocal  # noqa: E402
from app.models import StoryTimelineFeature  # noqa: E402
from app.services.timeline_audio import generate_audio  # noqa: E402
from app.services.timeline_narrative import (  # noqa: E402
    TimelineNarrativeError,
    call_claude_spoken_script_only,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# From audit_spoken_script_pattern.py's 2026-09-17 run — the 6 rows on the
# old, pre-emotion-tag spoken_script pattern.
ANCHOR_CLUSTER_IDS = [77801, 81799, 88463, 89409, 92564, 95712]


async def run(dry_run: bool) -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(StoryTimelineFeature).where(
                StoryTimelineFeature.anchor_cluster_id.in_(ANCHOR_CLUSTER_IDS)
            )
        )
        rows = result.scalars().all()
        found_ids = {row.anchor_cluster_id for row in rows}
        missing = set(ANCHOR_CLUSTER_IDS) - found_ids
        if missing:
            logger.warning("anchor_cluster_ids not found in DB: %s", sorted(missing))
        logger.info("%d/%d target rows found", len(rows), len(ANCHOR_CLUSTER_IDS))

        for row in rows:
            if not row.coherent:
                logger.warning(
                    "row id=%s anchor=%s: coherent=%s, skipping (spoken_script is never generated for "
                    "incoherent chains)", row.id, row.anchor_cluster_id, row.coherent,
                )
                continue
            if not row.context or not row.beats:
                logger.warning(
                    "row id=%s anchor=%s: coherent but missing context/beats, skipping",
                    row.id, row.anchor_cluster_id,
                )
                continue

            logger.info(
                "row id=%s anchor=%s: regenerating spoken_script (old pattern) + audio",
                row.id, row.anchor_cluster_id,
            )

            if dry_run:
                continue

            beat_narrations = [b.get("narration", "") for b in row.beats]
            try:
                spoken_script = await call_claude_spoken_script_only(row.context, beat_narrations)
            except TimelineNarrativeError as e:
                logger.error("row id=%s: spoken_script generation failed: %s", row.id, e)
                continue
            if len(spoken_script.get("beats") or []) != len(beat_narrations):
                logger.error(
                    "row id=%s: spoken beat count (%d) != written beat count (%d), skipping",
                    row.id, len(spoken_script.get("beats") or []), len(beat_narrations),
                )
                continue

            row.spoken_script = spoken_script

            try:
                audio_result = await generate_audio(row.anchor_cluster_id, spoken_script)
            except Exception as e:  # noqa: BLE001 - a TTS/upload failure must never abort the whole batch
                logger.warning("row id=%s: audio generation raised: %s", row.id, e)
                audio_result = None

            if audio_result is None:
                logger.warning("row id=%s: audio generation failed, spoken_script saved but audio left as-is", row.id)
            else:
                row.audio_url = audio_result["audio_url"]
                row.audio_duration_seconds = audio_result["audio_duration_seconds"]
                row.audio_beat_offsets = audio_result["audio_beat_offsets"]
                row.spoken_script_hash = audio_result["spoken_script_hash"]
                row.audio_generated_at = datetime.now(timezone.utc)
                logger.info("row id=%s: audio generated (%ss)", row.id, audio_result["audio_duration_seconds"])

            await session.commit()

        if dry_run:
            logger.info("dry run — no writes made")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.dry_run))
