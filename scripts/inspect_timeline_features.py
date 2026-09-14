"""Quick read-only dump of story_timeline_features — for eyeballing what
scripts/build_story_timelines.py produced. Not part of any request path.

Usage:
    python3 scripts/inspect_timeline_features.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select  # noqa: E402

from app.database import AsyncSessionLocal  # noqa: E402
from app.models import StoryTimelineFeature  # noqa: E402


async def main() -> None:
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(select(StoryTimelineFeature))).scalars().all()
        for r in rows:
            print(f"{r.id} anchor={r.anchor_cluster_id} editorial={r.is_editorial_pick} "
                  f"coherent={r.coherent} top={r.last_seen_in_top}")
            print(f"  title: {r.title}")
            print(f"  context: {(r.context or '')[:200]}")
            print(f"  audio_url: {r.audio_url}")
            print(f"  audio_duration_seconds: {r.audio_duration_seconds}")
            print(f"  audio_beat_offsets: {r.audio_beat_offsets}")
            print(f"  spoken_script_hash: {r.spoken_script_hash}")
            print(f"  audio_generated_at: {r.audio_generated_at}")
            print()


if __name__ == "__main__":
    asyncio.run(main())
