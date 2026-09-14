"""One-off: synthesize the same sample line in a handful of Gemini TTS voices
so a voice can be picked by ear for the Timeline narration feature (see
app/services/timeline_audio.py's VOICE_NAME). Not part of the production
pipeline — run manually, inspect the output files, then delete.

Usage (from the running container / venv with GEMINI_API_KEY set):
    python3 scripts/test_voice_samples.py
Writes voice_sample_<name>.m4a into the current directory for each voice.
"""
import asyncio
import sys

sys.path.insert(0, ".")

from app.services.timeline_audio import synthesize, _encode_pcm_to_m4a  # noqa: E402

SAMPLE_TEXT = (
    "So here's where things stand: after weeks of back and forth, the two "
    "sides finally reached an agreement on Tuesday, though a few key details "
    "are still being worked out."
)

VOICES = ["Kore", "Puck", "Charon", "Zephyr", "Fenrir", "Leda", "Aoede"]


async def main():
    for voice_name in VOICES:
        # synthesize() reads VOICE_NAME from the module at import time, so
        # patch it per-call rather than importing it as a constant here.
        import app.services.timeline_audio as ta
        ta.VOICE_NAME = voice_name
        print(f"Synthesizing {voice_name}...")
        pcm = await synthesize(SAMPLE_TEXT)
        if pcm is None:
            print(f"  FAILED: {voice_name}")
            continue
        encoded = await _encode_pcm_to_m4a(pcm)
        if encoded is None:
            print(f"  ENCODE FAILED: {voice_name}")
            continue
        out_path = f"voice_sample_{voice_name}.m4a"
        with open(out_path, "wb") as f:
            f.write(encoded)
        print(f"  wrote {out_path} ({len(encoded)} bytes)")


if __name__ == "__main__":
    asyncio.run(main())
