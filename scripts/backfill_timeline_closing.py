"""One-off backfill: add the "closing" wrap-up line + re-synthesize audio
(picking up the new randomized voice pool, see app/services/timeline_audio.py)
for existing story_timeline_features rows that predate both features.

Does NOT touch title/context/beats — only appends a closing line to the
stored spoken_script (via timeline_narrative.call_claude_closing, a small
targeted call, not a full narrative regeneration) and re-runs TTS. Rows
that already have a "closing" in their spoken_script are skipped, so this
is safe to re-run.

Usage:
    python3 scripts/backfill_timeline_closing.py            # do it
    python3 scripts/backfill_timeline_closing.py --dry-run  # report only
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
from app.services.timeline_audio import generate_audio, script_hash  # noqa: E402
from app.services.timeline_narrative import TimelineNarrativeError, call_claude_closing  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def run(dry_run: bool) -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(StoryTimelineFeature).where(
                StoryTimelineFeature.coherent.is_(True),
                StoryTimelineFeature.spoken_script.isnot(None),
            )
        )
        rows = result.scalars().all()

        candidates = [
            r for r in rows
            if r.spoken_script and r.spoken_script.get("intro") and r.spoken_script.get("beats")
            and not r.spoken_script.get("closing")
        ]
        logger.info("%d of %d coherent rows with a spoken_script need a closing line", len(candidates), len(rows))

        for row in candidates:
            intro = row.spoken_script["intro"]
            beats = row.spoken_script["beats"]
            logger.info("row id=%s anchor=%s: generating closing line", row.id, row.anchor_cluster_id)

            if dry_run:
                continue

            try:
                closing = await call_claude_closing(intro, beats)
            except TimelineNarrativeError as e:
                logger.error("row id=%s: closing generation failed: %s", row.id, e)
                continue

            new_spoken_script = dict(row.spoken_script)
            new_spoken_script["closing"] = closing
            row.spoken_script = new_spoken_script

            try:
                audio_result = await generate_audio(row.anchor_cluster_id, new_spoken_script)
            except Exception as e:  # noqa: BLE001 - a TTS/upload failure must never abort the whole backfill
                logger.warning("row id=%s: audio generation raised: %s", row.id, e)
                audio_result = None

            if audio_result is None:
                logger.warning("row id=%s: audio generation failed, spoken_script updated but audio left as-is", row.id)
            else:
                row.audio_url = audio_result["audio_url"]
                row.audio_duration_seconds = audio_result["audio_duration_seconds"]
                row.audio_beat_offsets = audio_result["audio_beat_offsets"]
                row.spoken_script_hash = audio_result["spoken_script_hash"]
                row.audio_generated_at = datetime.now(timezone.utc)
                logger.info("row id=%s: audio regenerated (%ss)", row.id, audio_result["audio_duration_seconds"])

            await session.commit()

        if dry_run:
            logger.info("dry run — no writes made")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.dry_run))
