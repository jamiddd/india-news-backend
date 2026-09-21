"""Rewrite the spoken script and re-synthesize the narration audio for SELECTED
story_timeline_features rows, in the current Sarvam voice.

Meant for rolling the Sarvam narration out to existing timelines a few at a
time (each row costs one Claude call plus roughly Rs 15-20 of Sarvam TTS), so
the rows to process must be named explicitly with --ids — there is
deliberately no "every row" mode. The ids are the row ids the public API
exposes as the timeline id (GET /api/v1/timelines/<id>); scripts/
find_timeline_story.py looks one up by title.

For each row it:
  1. rewrites the spoken script from the row's existing written context/beats
     (call_claude_spoken_script_only) in the current style, and
  2. voices it (generate_audio) and stores the new audio_url / duration /
     beat offsets.
Rows already narrated in the current format are skipped unless --force.
The existing audio is left untouched if any step fails.

Usage (inside the app container, e.g. `podman exec -it news_app_prod ...`):
    python3 scripts/backfill_timeline_audio.py --ids 24 --dry-run
    python3 scripts/backfill_timeline_audio.py --ids 24
    python3 scripts/backfill_timeline_audio.py --ids 21 22 --force
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
from app.services.timeline_audio import SCRIPT_VERSION, generate_audio  # noqa: E402
from app.services.timeline_narrative import TimelineNarrativeError, call_claude_spoken_script_only  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _is_current(row: StoryTimelineFeature) -> bool:
    return bool(row.audio_url) and (row.spoken_script or {}).get("version") == SCRIPT_VERSION


async def run(ids: list[int], dry_run: bool, force: bool) -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(StoryTimelineFeature).where(StoryTimelineFeature.id.in_(ids)))
        rows = {row.id: row for row in result.scalars().all()}

        for row_id in ids:
            row = rows.get(row_id)
            if row is None:
                logger.warning("id=%s: no such row", row_id)
                continue
            label = f"id={row.id} anchor={row.anchor_cluster_id} {row.title!r}"
            if not row.coherent or not row.context or not row.beats:
                logger.warning("%s: not a coherent timeline with context/beats, skipping", label)
                continue
            if _is_current(row) and not force:
                logger.info("%s: already narrated in the current format, skipping (use --force to redo)", label)
                continue

            logger.info("%s: %d beats, has audio=%s", label, len(row.beats), bool(row.audio_url))
            if dry_run:
                continue

            try:
                spoken_script = await call_claude_spoken_script_only(row.context, row.beats)
            except TimelineNarrativeError as e:
                logger.error("%s: spoken script generation failed: %s", label, e)
                continue

            try:
                audio_result = await generate_audio(row.anchor_cluster_id, spoken_script)
            except Exception as e:  # noqa: BLE001 - one bad row must not abort the rest
                logger.warning("%s: audio generation raised: %s", label, e)
                audio_result = None

            if audio_result is None:
                # Keep the old script and audio pair consistent: don't save the
                # new spoken_script without audio, or the next narrator cycle
                # would treat it as "unchanged" and never voice it.
                logger.warning("%s: audio generation failed, row left unchanged", label)
                continue

            row.spoken_script = spoken_script
            row.audio_url = audio_result["audio_url"]
            row.audio_duration_seconds = audio_result["audio_duration_seconds"]
            row.audio_beat_offsets = audio_result["audio_beat_offsets"]
            row.spoken_script_hash = audio_result["spoken_script_hash"]
            row.audio_generated_at = datetime.now(timezone.utc)
            await session.commit()
            logger.info("%s: audio generated (%ss) -> %s", label, audio_result["audio_duration_seconds"], audio_result["audio_url"])

        if dry_run:
            logger.info("dry run — nothing written, no API calls made")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ids", type=int, nargs="+", required=True, help="story_timeline_features row ids to process")
    parser.add_argument("--dry-run", action="store_true", help="report what would be processed; no API calls, no writes")
    parser.add_argument("--force", action="store_true", help="redo rows already narrated in the current format")
    args = parser.parse_args()
    asyncio.run(run(args.ids, args.dry_run, args.force))
