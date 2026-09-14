"""One-off backfill: generate a spoken_script (+ audio) for existing
story_timeline_features rows that predate the spoken_script feature
entirely — coherent rows whose chain hasn't changed since before that
feature shipped, so build_story_timelines.py's "skip if unchanged since
last generation" logic never re-ran the narrative prompt for them and
spoken_script is still null.

Unlike backfill_timeline_closing.py (which only adds a missing "closing"
to an EXISTING spoken_script), this generates the whole spoken_script from
the row's already-written context/beats via
timeline_narrative.call_claude_spoken_script_only — title/context/beats
themselves are left untouched.

Usage:
    python3 scripts/backfill_timeline_spoken_script.py            # do it
    python3 scripts/backfill_timeline_spoken_script.py --dry-run  # report only
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
from app.services.timeline_narrative import TimelineNarrativeError, call_claude_spoken_script_only  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def run(dry_run: bool) -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(StoryTimelineFeature).where(
                StoryTimelineFeature.coherent.is_(True),
                StoryTimelineFeature.spoken_script.is_(None),
            )
        )
        rows = [r for r in result.scalars().all() if r.context and r.beats]
        logger.info("%d coherent rows missing spoken_script entirely", len(rows))

        for row in rows:
            beat_narrations = [b.get("narration", "") for b in row.beats]
            logger.info(
                "row id=%s anchor=%s (%d beats): generating spoken_script",
                row.id, row.anchor_cluster_id, len(beat_narrations),
            )

            if dry_run:
                continue

            try:
                spoken_script = await call_claude_spoken_script_only(row.context, beat_narrations)
            except TimelineNarrativeError as e:
                logger.error("row id=%s: spoken_script generation failed: %s", row.id, e)
                continue

            if len(spoken_script.get("beats") or []) != len(beat_narrations):
                logger.error(
                    "row id=%s: spoken beat count (%d) doesn't match written beat count (%d), skipping audio",
                    row.id, len(spoken_script.get("beats") or []), len(beat_narrations),
                )
                continue

            row.spoken_script = spoken_script

            try:
                audio_result = await generate_audio(row.anchor_cluster_id, spoken_script)
            except Exception as e:  # noqa: BLE001 - a TTS/upload failure must never abort the whole backfill
                logger.warning("row id=%s: audio generation raised: %s", row.id, e)
                audio_result = None

            if audio_result is None:
                logger.warning("row id=%s: audio generation failed, spoken_script saved but no audio", row.id)
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
