"""Backfill covering coherent timeline rows, regardless of which gap a row
has:

  - no spoken_script at all (chain hasn't changed since before that
    feature shipped, so build_story_timelines.py's "skip if unchanged"
    logic never regenerated it) -> generate the full spoken_script from
    the row's existing written context/beats via
    timeline_narrative.call_claude_spoken_script_only
  - has a spoken_script but it predates "style_scene"/"style_context" (the
    per-story TTS delivery register added 2026-09-15 — see
    timeline_narrative.py's SYSTEM_PROMPT) -> also regenerate the full
    spoken_script via call_claude_spoken_script_only, since style can't be
    retrofitted onto already-written spoken text piecemeal
  - has a spoken_script with style fields but it predates "closing" ->
    generate just the closing line via timeline_narrative.call_claude_closing
  - already complete -> skipped

Either way, ends by calling generate_audio to (re)synthesize the audio,
which also means every row picks up the current TTS_MODEL/VOICE_NAMES in
app/services/timeline_audio.py (e.g. the 2026-09-14 gemini-3.1-flash-tts-preview
switch) even if its spoken_script itself didn't need to change.

Default scope is rows with NO audio_url yet — the priority right now is
filling gaps left by yesterday's Gemini rate-limit hits, not re-spending
calls on rows that already have working audio. Pass --all for the
occasional full-catalog pass (picks up style_scene/style_context on rows
that already have audio) — safe to run since this app only has ~15 coherent
timeline rows total, well under Gemini's ~100 RPD observed quota.

Usage:
    python3 scripts/backfill_all_timeline_audio.py             # rows missing audio only
    python3 scripts/backfill_all_timeline_audio.py --all       # every coherent row
    python3 scripts/backfill_all_timeline_audio.py --dry-run   # report only (respects --all)
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
    call_claude_closing,
    call_claude_spoken_script_only,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def _ensure_spoken_script(row: StoryTimelineFeature) -> dict | None:
    """Returns a complete spoken_script (with closing, and style_scene/
    style_context) for this row, or None if it can't be produced (missing
    context/beats, or generation failed). Never mutates `row` — the caller
    decides when to assign.

    Missing spoken_script entirely and missing style fields are handled the
    same way (a full call_claude_spoken_script_only regeneration) — style
    can't be bolted onto already-written spoken text after the fact, it has
    to be decided together with how that text is written."""
    needs_full_regen = not row.spoken_script or "style_scene" not in row.spoken_script
    if needs_full_regen:
        if not row.context or not row.beats:
            logger.warning("row id=%s: coherent but missing context/beats, skipping", row.id)
            return None
        beat_narrations = [b.get("narration", "") for b in row.beats]
        try:
            spoken_script = await call_claude_spoken_script_only(row.context, beat_narrations)
        except TimelineNarrativeError as e:
            logger.error("row id=%s: spoken_script generation failed: %s", row.id, e)
            return None
        if len(spoken_script.get("beats") or []) != len(beat_narrations):
            logger.error(
                "row id=%s: spoken beat count (%d) != written beat count (%d), skipping",
                row.id, len(spoken_script.get("beats") or []), len(beat_narrations),
            )
            return None
        return spoken_script

    if not row.spoken_script.get("closing"):
        try:
            closing = await call_claude_closing(row.spoken_script["intro"], row.spoken_script["beats"])
        except TimelineNarrativeError as e:
            logger.error("row id=%s: closing generation failed: %s", row.id, e)
            return None
        return {**row.spoken_script, "closing": closing}

    return dict(row.spoken_script)  # already complete — still re-synthesized below


async def run(dry_run: bool, include_all: bool) -> None:
    async with AsyncSessionLocal() as session:
        query = select(StoryTimelineFeature).where(StoryTimelineFeature.coherent.is_(True))
        if not include_all:
            # Default scope: rows with no audio at all — filling gaps from
            # a rate-limited run, not re-spending calls on rows that already
            # have working audio. Pass --all for the occasional full pass.
            query = query.where(StoryTimelineFeature.audio_url.is_(None))
        result = await session.execute(query)
        rows = result.scalars().all()
        logger.info(
            "%d %s rows selected", len(rows), "coherent" if include_all else "coherent, missing-audio"
        )

        for row in rows:
            has_script = bool(row.spoken_script)
            has_style = has_script and "style_scene" in row.spoken_script
            has_closing = has_script and bool(row.spoken_script.get("closing"))
            status = (
                "complete" if (has_style and has_closing)
                else "missing style" if has_script
                else "missing spoken_script"
            )
            logger.info(
                "row id=%s anchor=%s: %s (audio=%s)",
                row.id, row.anchor_cluster_id, status, "yes" if row.audio_url else "no",
            )

            if dry_run:
                continue

            spoken_script = await _ensure_spoken_script(row)
            if spoken_script is None:
                continue
            row.spoken_script = spoken_script

            try:
                audio_result = await generate_audio(row.anchor_cluster_id, spoken_script)
            except Exception as e:  # noqa: BLE001 - a TTS/upload failure must never abort the whole backfill
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
    parser.add_argument(
        "--all", action="store_true",
        help="include rows that already have audio (default: only rows missing audio)",
    )
    args = parser.parse_args()
    asyncio.run(run(args.dry_run, args.all))
