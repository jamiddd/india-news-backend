"""One-off audit: for every story_timeline_features row, report whether it
has a narrative, a spoken_script, and audio — and whether that audio was
generated before or after the Sep 14 2026 13:39 IST switch from
gemini-2.5-flash-preview-tts to gemini-3.1-flash-tts-preview (the model
itself isn't stored per-row, so this timestamp cutoff is the only way to
tell old vs. new without re-listening). Read-only — makes no changes.

Run inside the app container:
    podman exec -it news_app_prod python3 scripts/audit_timeline_audio.py
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import StoryTimelineFeature

# Sep 14 2026 13:39:36 +05:30 -> UTC. Rows with audio_generated_at before
# this used gemini-2.5-flash-preview-tts; at/after, gemini-3.1-flash-tts-preview.
MODEL_SWITCH_UTC = datetime(2026, 9, 14, 8, 9, 36, tzinfo=timezone.utc)


async def main() -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(StoryTimelineFeature).order_by(StoryTimelineFeature.anchor_cluster_id)
        )
        rows = result.scalars().all()

    no_narrative = []
    no_script = []
    no_audio = []
    old_model = []
    new_model = []
    near_cutoff = []

    for row in rows:
        if not row.narrative_generated_at:
            no_narrative.append(row)
            continue
        if not row.spoken_script:
            no_script.append(row)
            continue
        if not row.audio_url or not row.audio_generated_at:
            no_audio.append(row)
            continue

        delta = (row.audio_generated_at - MODEL_SWITCH_UTC).total_seconds()
        if abs(delta) < 1800:  # within 30 min of the switch — flag for manual check
            near_cutoff.append(row)
        elif row.audio_generated_at < MODEL_SWITCH_UTC:
            old_model.append(row)
        else:
            new_model.append(row)

    print(f"Total rows: {len(rows)}\n")

    def show(label: str, group: list) -> None:
        print(f"{label}: {len(group)}")
        for r in group:
            gen_at = r.audio_generated_at.isoformat() if r.audio_generated_at else "-"
            print(f"  anchor_cluster_id={r.anchor_cluster_id}  audio_generated_at={gen_at}")
        print()

    show("No narrative generated yet", no_narrative)
    show("Narrative but no spoken_script (script gen failed/skipped)", no_script)
    show("spoken_script but no audio (synth failed)", no_audio)
    show("OLD MODEL audio (gemini-2.5-flash-preview-tts) — needs regen", old_model)
    show("NEW MODEL audio (gemini-3.1-flash-tts-preview) — already fine", new_model)
    show("Within 30min of the model switch — verify manually", near_cutoff)


if __name__ == "__main__":
    asyncio.run(main())
