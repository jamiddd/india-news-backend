"""Narrate SELECTED story_timeline_features rows from the command line, in the
current Sarvam voice — the same thing as the admin page's "Generate narration"
button (app/services/timeline_narration.py), just for several ids at once.

Meant for rolling the narration out to existing timelines a few at a time (each
row costs one Claude call plus roughly Rs 15-20 of Sarvam TTS), so the rows
must be named explicitly with --ids — there is deliberately no "every row"
mode. The ids are the row ids the public API exposes as the timeline id
(GET /api/v1/timelines/<id>); scripts/find_timeline_story.py looks one up by
title. Rows already narrated in the current format are skipped unless --force.
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

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import AsyncSessionLocal  # noqa: E402
from app.models import StoryTimelineFeature  # noqa: E402
from app.services.timeline_audio import SCRIPT_VERSION  # noqa: E402
from app.services.timeline_narration import can_narrate, narrate_row  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def run(ids: list[int], dry_run: bool, force: bool) -> None:
    for row_id in ids:
        async with AsyncSessionLocal() as session:
            row = await session.get(StoryTimelineFeature, row_id)
            if row is None:
                logger.warning("id=%s: no such row", row_id)
                continue
            label = f"id={row.id} anchor={row.anchor_cluster_id} {row.title!r}"
            reason = can_narrate(row)
            if reason:
                logger.warning("%s: %s, skipping", label, reason)
                continue
            current = bool(row.audio_url) and (row.spoken_script or {}).get("version") == SCRIPT_VERSION
            if current and not force:
                logger.info("%s: already narrated in the current format, skipping (use --force to redo)", label)
                continue
            logger.info("%s: %d beats, has audio=%s", label, len(row.beats), bool(row.audio_url))
        if dry_run:
            continue
        outcome = await narrate_row(row_id)
        logger.info("%s: %s - %s", label, "OK" if outcome.ok else "FAILED", outcome.message)

    if dry_run:
        logger.info("dry run - nothing written, no API calls made")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ids", type=int, nargs="+", required=True, help="story_timeline_features row ids to process")
    parser.add_argument("--dry-run", action="store_true", help="report what would be processed; no API calls, no writes")
    parser.add_argument("--force", action="store_true", help="redo rows already narrated in the current format")
    args = parser.parse_args()
    asyncio.run(run(args.ids, args.dry_run, args.force))
