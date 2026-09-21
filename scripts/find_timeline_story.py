"""One-off lookup: find a story_timeline_features row by (partial,
case-insensitive) title match, so its id can be fed into
regenerate_one_timeline_audio.py <row_id>.

Why this exists: when a listener flags "the voice sounds wrong on that
one story" there's no id in hand, only a fragment of the headline as
displayed in the app. This does a simple ILIKE search over `title` so
you don't have to eyeball the whole table.

Usage:
    python3 scripts/find_timeline_story.py "us-iran gulf escalation"
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select  # noqa: E402

from app.database import AsyncSessionLocal  # noqa: E402
from app.models import StoryTimelineFeature  # noqa: E402


async def run(fragment: str) -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(StoryTimelineFeature).where(
                StoryTimelineFeature.title.ilike(f"%{fragment}%")
            )
        )
        rows = result.scalars().all()
        if not rows:
            print(f"no story_timeline_features row with title matching {fragment!r}")
            return
        for row in rows:
            print(f"id={row.id} anchor_cluster_id={row.anchor_cluster_id}")
            print(f"  title: {row.title}")
            print(f"  audio_url: {row.audio_url}")
            print(f"  audio_generated_at: {row.audio_generated_at}")
            print(f"  spoken_script_hash: {row.spoken_script_hash}")
            print()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print('usage: python3 scripts/find_timeline_story.py "<title fragment>"')
        sys.exit(1)
    asyncio.run(run(sys.argv[1]))
