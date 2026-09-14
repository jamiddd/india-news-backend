"""One-off: regenerate spoken_script + audio for exactly ONE
story_timeline_features row, identified by its row id. Used to test a
prompt-style change (e.g. the 2026-09-14 podcast/radio-host delivery
tweak in timeline_narrative.py) against a single real story before
deciding whether to run it across every row.

Always regenerates the spoken_script from the row's existing written
context/beats (via call_claude_spoken_script_only), even if one already
exists — unlike backfill_all_timeline_audio.py, which only fills gaps.

Usage:
    python3 scripts/regenerate_one_timeline_audio.py <row_id>
"""
import asyncio
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select  # noqa: E402

from app.database import AsyncSessionLocal  # noqa: E402
from app.models import StoryTimelineFeature  # noqa: E402
from app.services.timeline_audio import generate_audio  # noqa: E402
from app.services.timeline_narrative import TimelineNarrativeError, call_claude_spoken_script_only  # noqa: E402


async def run(row_id: int) -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(StoryTimelineFeature).where(StoryTimelineFeature.id == row_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            print(f"no row with id={row_id}")
            return
        if not row.context or not row.beats:
            print(f"row id={row_id} missing context/beats, can't regenerate")
            return

        print(f"row id={row.id} anchor={row.anchor_cluster_id}: {row.title}")
        beat_narrations = [b.get("narration", "") for b in row.beats]

        try:
            spoken_script = await call_claude_spoken_script_only(row.context, beat_narrations)
        except TimelineNarrativeError as e:
            print(f"spoken_script generation failed: {e}")
            return

        if len(spoken_script.get("beats") or []) != len(beat_narrations):
            print(
                f"spoken beat count ({len(spoken_script.get('beats') or [])}) != "
                f"written beat count ({len(beat_narrations)}), aborting"
            )
            return

        row.spoken_script = spoken_script

        try:
            audio_result = await generate_audio(row.anchor_cluster_id, spoken_script)
        except Exception as e:  # noqa: BLE001
            print(f"audio generation raised: {e}")
            audio_result = None

        if audio_result is None:
            print("audio generation failed, spoken_script saved but no audio")
        else:
            row.audio_url = audio_result["audio_url"]
            row.audio_duration_seconds = audio_result["audio_duration_seconds"]
            row.audio_beat_offsets = audio_result["audio_beat_offsets"]
            row.spoken_script_hash = audio_result["spoken_script_hash"]
            row.audio_generated_at = datetime.now(timezone.utc)
            print(f"audio regenerated: {audio_result['audio_url']} ({audio_result['audio_duration_seconds']}s)")

        await session.commit()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python3 scripts/regenerate_one_timeline_audio.py <row_id>")
        sys.exit(1)
    asyncio.run(run(int(sys.argv[1])))
