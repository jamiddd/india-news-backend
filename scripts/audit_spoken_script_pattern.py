"""One-off audit: for every story_timeline_features row that has a
spoken_script, check whether it matches the CURRENT schema (with
style_scene/style_context keys and inline [cue] delivery tags in
intro/beats/closing — see timeline_narrative.py's SYSTEM_PROMPT) or an
older, plain-text version from before those were added. A row on the old
pattern has a script that was written before emotion tags existed, so its
audio (synthesized from that plain script) doesn't carry emotion either —
regardless of which Gemini model rendered it. Read-only — makes no changes.

Run inside the app container:
    podman exec -it news_app_prod python3 scripts/audit_spoken_script_pattern.py
"""
from __future__ import annotations

import asyncio
import re

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import StoryTimelineFeature

CUE_TAG_RE = re.compile(r"\[[a-z][a-z \-]*\]")


def classify(spoken_script: dict) -> tuple[str, list[str]]:
    """Returns (verdict, reasons). verdict is 'current', 'old', or 'unclear'."""
    reasons = []

    if not isinstance(spoken_script, dict):
        return "old", ["spoken_script is not a dict"]

    if "style_scene" not in spoken_script or not spoken_script.get("style_scene"):
        reasons.append("missing style_scene")
    if "style_context" not in spoken_script or not spoken_script.get("style_context"):
        reasons.append("missing style_context")
    if "closing" not in spoken_script or not spoken_script.get("closing"):
        reasons.append("missing closing")

    text_parts = []
    if spoken_script.get("intro"):
        text_parts.append(str(spoken_script["intro"]))
    for beat in spoken_script.get("beats") or []:
        text_parts.append(str(beat))
    if spoken_script.get("closing"):
        text_parts.append(str(spoken_script["closing"]))
    joined = " ".join(text_parts)

    has_cue_tags = bool(CUE_TAG_RE.search(joined))
    if not has_cue_tags:
        reasons.append("no [cue] tags found in intro/beats/closing")

    if not reasons:
        return "current", reasons
    return "old", reasons


async def main() -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(StoryTimelineFeature)
            .where(StoryTimelineFeature.spoken_script.isnot(None))
            .order_by(StoryTimelineFeature.anchor_cluster_id)
        )
        rows = result.scalars().all()

    current_rows = []
    old_rows = []

    for row in rows:
        verdict, reasons = classify(row.spoken_script)
        has_audio = bool(row.audio_url)
        entry = (row.anchor_cluster_id, has_audio, reasons)
        if verdict == "current":
            current_rows.append(entry)
        else:
            old_rows.append(entry)

    print(f"Rows with a spoken_script: {len(rows)}\n")

    print(f"CURRENT pattern (has emotion tags): {len(current_rows)}")
    for anchor_id, has_audio, _ in current_rows:
        print(f"  anchor_cluster_id={anchor_id}  has_audio={has_audio}")
    print()

    print(f"OLD pattern (plain script, no emotion tags) — needs script+audio regen: {len(old_rows)}")
    for anchor_id, has_audio, reasons in old_rows:
        print(f"  anchor_cluster_id={anchor_id}  has_audio={has_audio}  reasons={reasons}")
    print()


if __name__ == "__main__":
    asyncio.run(main())
