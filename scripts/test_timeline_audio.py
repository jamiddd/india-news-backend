"""Isolation test for app/services/timeline_audio.py — verifies the Gemini
TTS call shape, PCM parameters, and the ffmpeg encode before any of it is
wired into the nightly generation cycle (see the "Verification" section of
the timeline-narration plan). Needs GEMINI_API_KEY in the environment;
needs no DATABASE_URL, no Supabase config, and does not touch the DB or
upload anything — it just writes a local .m4a file to listen to.

Usage:
    GEMINI_API_KEY=... python3 scripts/test_timeline_audio.py
    GEMINI_API_KEY=... python3 scripts/test_timeline_audio.py "Custom sentence to synthesize."

Also verifies computed vs. actual chunk duration (via ffprobe) for two
chunks concatenated together, since drift here is exactly what would make
beat highlighting run ahead of or behind the voice in the app.
"""
import asyncio
import os
import subprocess
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.timeline_audio import (  # noqa: E402
    BYTES_PER_SECOND,
    _encode_pcm_to_m4a,
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


if __name__ == "__main__":
    asyncio.run(main())
