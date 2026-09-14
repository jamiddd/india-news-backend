"""Isolation test for app/services/timeline_audio.py — verifies the Gemini
TTS call shape, PCM parameters, and the ffmpeg encode before any of it is
wired into the nightly generation cycle (see the "Verification" section of
the timeline-narration plan). Needs GEMINI_API_KEY in the environment;
needs no DATABASE_URL and does not touch the DB.

Usage:
    GEMINI_API_KEY=... python3 scripts/test_timeline_audio.py
    GEMINI_API_KEY=... python3 scripts/test_timeline_audio.py "Custom sentence to synthesize."

If SUPABASE_URL and SUPABASE_SERVICE_KEY are also set, additionally runs
generate_audio() end-to-end against the real timeline-audio bucket (upload
included) and fetches the result back over its public URL to confirm it's
actually reachable — uploads under an obviously-fake anchor_cluster_id
(999999999) so it's easy to find and delete from the bucket afterward.
Without those two vars, only the local synthesize/encode steps run.

Also verifies computed vs. actual chunk duration (via ffprobe) for two
chunks concatenated together, since drift here is exactly what would make
beat highlighting run ahead of or behind the voice in the app.
"""
import asyncio
import os
import subprocess
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import httpx  # noqa: E402

from app.services.timeline_audio import (  # noqa: E402
    BYTES_PER_SECOND,
    _encode_pcm_to_m4a,
    generate_audio,
    synthesize,
)

DEFAULT_TEXT_A = (
    "So here's the deal. Over about two weeks in September, the AI world "
    "went from hyping its most powerful model yet to openly debating "
    "whether it should slow down entirely."
)
DEFAULT_TEXT_B = (
    "It starts on September third, when the company begins rolling out "
    "its newest model to a small group of business customers first."
)


def ffprobe_duration_seconds(path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


async def main():
    if not os.environ.get("GEMINI_API_KEY"):
        print("GEMINI_API_KEY not set in the environment.")
        sys.exit(1)

    text_a = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TEXT_A
    text_b = DEFAULT_TEXT_B

    print(f"--- synthesizing chunk A ({len(text_a)} chars) ---")
    pcm_a = await synthesize(text_a)
    if pcm_a is None:
        print("FAILED: synthesize() returned None for chunk A — check the API key/model/response shape.")
        sys.exit(1)
    duration_a = len(pcm_a) / BYTES_PER_SECOND
    print(f"chunk A: {len(pcm_a)} bytes, computed duration {duration_a:.2f}s")

    # Saved and encoded standalone (before concatenation) so a listener can
    # tell whether a single isolated chunk is itself truncated, vs. only
    # sounding cut off at the zero-gap boundary where it's glued to chunk B.
    encoded_a = await _encode_pcm_to_m4a(pcm_a)
    if encoded_a is not None:
        a_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "test_timeline_audio_chunk_a.m4a"))
        with open(a_path, "wb") as f:
            f.write(encoded_a)
        print(f"wrote standalone chunk A to {a_path} — listen to THIS first")

    print(f"--- synthesizing chunk B ({len(text_b)} chars) ---")
    pcm_b = await synthesize(text_b)
    if pcm_b is None:
        print("FAILED: synthesize() returned None for chunk B.")
        sys.exit(1)
    duration_b = len(pcm_b) / BYTES_PER_SECOND
    print(f"chunk B: {len(pcm_b)} bytes, computed duration {duration_b:.2f}s")

    computed_total = duration_a + duration_b
    print(f"computed total duration (A+B): {computed_total:.2f}s")

    print("--- encoding concatenated PCM via ffmpeg ---")
    concatenated = pcm_a + pcm_b
    encoded = await _encode_pcm_to_m4a(concatenated)
    if encoded is None:
        print("FAILED: ffmpeg encode returned None — check ffmpeg is installed and the pipe args.")
        sys.exit(1)

    out_path = os.path.join(os.path.dirname(__file__), "..", "test_timeline_audio_output.m4a")
    out_path = os.path.abspath(out_path)
    with open(out_path, "wb") as f:
        f.write(encoded)
    print(f"wrote {len(encoded)} bytes to {out_path}")

    actual_duration = ffprobe_duration_seconds(out_path)
    drift = actual_duration - computed_total
    print(f"ffprobe actual duration: {actual_duration:.2f}s (drift from computed: {drift:+.2f}s)")
    if abs(drift) > 0.15:
        print("WARNING: drift exceeds 150ms — beat offsets computed from byte length may not match "
              "the encoded file closely enough for tight highlighting sync.")
    else:
        print("OK: computed offsets track the encoded file closely.")

    print(f"\nPlay it: scp this file to your machine and open {os.path.basename(out_path)}, "
          "or if running locally just open it directly.")

    if not (os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_KEY")):
        print("\nSUPABASE_URL/SUPABASE_SERVICE_KEY not set — skipping the real upload test.")
        return

    print("\n--- end-to-end: generate_audio() against the real timeline-audio bucket ---")
    fake_anchor_cluster_id = 999999999  # obviously-fake id, easy to spot/delete in the bucket later
    spoken_script = {"intro": text_a, "beats": [text_b]}
    result = await generate_audio(fake_anchor_cluster_id, spoken_script)
    if result is None:
        print("FAILED: generate_audio() returned None — check Supabase config/bucket name/service key.")
        sys.exit(1)
    print(f"generate_audio() result: {result}")

    print(f"--- fetching {result['audio_url']} to confirm it's publicly reachable ---")
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(result["audio_url"])
    print(f"GET {result['audio_url']} -> {response.status_code}, {len(response.content)} bytes")
    if response.status_code == 200 and len(response.content) > 0:
        print("OK: uploaded object is publicly fetchable.")
    else:
        print("WARNING: upload succeeded but the public URL didn't return audio — check bucket public flag/policies.")


if __name__ == "__main__":
    asyncio.run(main())
