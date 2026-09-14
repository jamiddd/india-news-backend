"""Spoken narration audio for the Timeline/Context tab.

Claude writes the spoken script (see timeline_narrative.py's spoken_script
field) — this module's only job is turning that script into a single
playable audio file: synthesize each chunk (intro + one per beat) with
Gemini TTS, concatenate the raw PCM, encode with ffmpeg, and upload to a
public Supabase Storage bucket (same project as editorial_backgrounds.py,
different bucket).

Gemini is a second vendor on a backend that has otherwise only ever called
Anthropic. Every function here returns None on any failure — missing config,
a TTS error, an upload error — and that must be non-fatal to the surrounding
narrative-generation cycle: a story with no audio renders exactly like a
story generated before this feature existed. See generate_for_chain in
scripts/build_story_timelines.py for how a None here is handled.

Per-chunk synthesis (rather than one call for the whole narration) isn't
just a workaround for Gemini TTS's small input window — a long saga would
not fit in one call — it's also what makes beat-level highlighting possible:
each chunk's duration becomes that beat's start offset in the concatenated
file. There's no word-level timestamp API, so beat-level is the finest
granularity available.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import tempfile
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Changing either of these changes every future narration's voice/model in
# one place — never inline the string elsewhere.
TTS_MODEL = "gemini-2.5-flash-preview-tts"
VOICE_NAME = "Kore"

TTS_API_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/{TTS_MODEL}:generateContent"
)

# Gemini TTS returns raw PCM at a fixed 24kHz, 16-bit (2 byte), mono —
# documented output format, not something we request. bytes / BYTES_PER_SECOND
# gives chunk duration without decoding anything.
PCM_SAMPLE_RATE = 24_000
PCM_SAMPLE_WIDTH = 2
PCM_CHANNELS = 1
BYTES_PER_SECOND = PCM_SAMPLE_RATE * PCM_SAMPLE_WIDTH * PCM_CHANNELS

AUDIO_CONTENT_TYPE = "audio/mp4"
AUDIO_FILE_EXTENSION = "m4a"


def _configured() -> bool:
    return bool(settings.GEMINI_API_KEY and settings.SUPABASE_URL and settings.SUPABASE_SERVICE_KEY)


def script_hash(spoken_script: dict) -> str:
    """Stable hash of the spoken script's actual content, used to skip
    re-synthesizing audio when nothing changed — see the 06:00 IST daily
    cycle cost concern in scripts/build_story_timelines.py. sort_keys makes
    this independent of dict ordering, which json.dumps does not otherwise
    guarantee across Python versions/callers."""
    canonical = json.dumps(spoken_script, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


async def synthesize(text: str, *, attempts: int = 3, timeout: float = 60) -> Optional[bytes]:
    """POST one chunk of text to Gemini TTS, return raw PCM bytes or None
    once all attempts are exhausted. Mirrors llm_gen.call_claude_json's
    retry-then-None contract rather than raising, since a single failed
    chunk should not be distinguishable from "TTS unavailable" to the
    caller — both mean "skip audio for this cycle"."""
    if not settings.GEMINI_API_KEY:
        return None
    payload = {
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": VOICE_NAME}}
            },
        },
    }
    for attempt in range(attempts):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    TTS_API_URL,
                    params={"key": settings.GEMINI_API_KEY},
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
            candidates = data.get("candidates") or []
            parts = (candidates[0].get("content", {}).get("parts") or []) if candidates else []
            inline_data = next((p["inlineData"] for p in parts if "inlineData" in p), None)
            if inline_data is None:
                raise ValueError(f"No inlineData in Gemini TTS response: {data!r}"[:2000])
            return base64.b64decode(inline_data["data"])
        except Exception as exc:  # noqa: BLE001 - any failure here just means "no audio this cycle"
            logger.warning("Gemini TTS attempt %s failed: %s: %s", attempt + 1, type(exc).__name__, exc)
    return None


async def _encode_pcm_to_m4a(pcm_bytes: bytes) -> Optional[bytes]:
    """Pipe raw PCM into ffmpeg, encode to a compressed AAC/m4a container.
    Raw WAV would be simpler but multiplies storage/egress for no
    perceptible quality gain on spoken narration.

    Writes to a temp file rather than stdout: an mp4 muxed straight to a
    pipe needs frag_keyframe+empty_moov (no seeking back to patch the moov
    atom), and that fragmented-mp4 output was confirmed to play back
    incomplete/truncated in real players (QuickTime, Finder preview) even
    though ffprobe parses its duration correctly — a fragmented mp4 is
    valid but not universally well-supported for playback. Letting ffmpeg
    seek back to write a normal moov atom (with +faststart so the moov sits
    before the audio data, for progressive playback/streaming from the
    Supabase public url) trades pure-streaming for actual compatibility,
    which matters far more here since this is written once and played many
    times by an Android media3 player."""
    try:
        with tempfile.NamedTemporaryFile(suffix=".m4a") as tmp:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-y",
                "-f", "s16le",
                "-ar", str(PCM_SAMPLE_RATE),
                "-ac", str(PCM_CHANNELS),
                "-i", "pipe:0",
                "-c:a", "aac",
                "-b:a", "64k",
                "-movflags", "+faststart",
                tmp.name,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate(input=pcm_bytes)
            if proc.returncode != 0:
                logger.warning("ffmpeg encode failed (rc=%s): %s", proc.returncode, stderr[-2000:])
                return None
            return tmp.read()
    except Exception as exc:  # noqa: BLE001
        logger.warning("ffmpeg invocation failed: %s: %s", type(exc).__name__, exc)
        return None


def _project_base_url() -> str:
    """Same origin-derivation as editorial_backgrounds.project_base_url —
    duplicated rather than imported to keep this module's only coupling to
    that one being the shared SUPABASE_URL/SUPABASE_SERVICE_KEY config, not
    an import of an unrelated feature module."""
    if not settings.SUPABASE_URL:
        return ""
    base = settings.SUPABASE_URL.strip().rstrip("/")
    for suffix in ("/rest/v1", "/storage/v1", "/auth/v1"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    return base.rstrip("/")


async def _upload_object(name: str, data: bytes, content_type: str) -> Optional[str]:
    """PUT one object to Supabase Storage and return its public url, or None
    on failure. No upload helper exists yet in editorial_backgrounds.py —
    that module only ever lists its bucket — so this is the first one."""
    base = _project_base_url()
    if not base:
        return None
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.put(
                f"{base}/storage/v1/object/{settings.TIMELINE_AUDIO_BUCKET}/{name}",
                headers={
                    "Authorization": f"Bearer {settings.SUPABASE_SERVICE_KEY}",
                    "apikey": settings.SUPABASE_SERVICE_KEY,
                    "Content-Type": content_type,
                    "x-upsert": "true",
                },
                content=data,
            )
            response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Supabase upload of %s failed: %s: %s", name, type(exc).__name__, exc)
        return None
    return f"{base}/storage/v1/object/public/{settings.TIMELINE_AUDIO_BUCKET}/{name}"


async def generate_audio(anchor_cluster_id: int, spoken_script: dict) -> Optional[dict]:
    """Synthesize + upload the full narration for one timeline pick.

    Returns {"audio_url", "audio_duration_seconds", "audio_beat_offsets",
    "spoken_script_hash"} on success, or None on any failure — a partial
    result (e.g. some beats synthesized, one failed) is treated as total
    failure rather than uploading a truncated narration, since a listener
    hearing the audio cut off mid-story is worse than no audio button at
    all this cycle. The next cycle will simply retry from scratch.
    """
    if not _configured():
        return None

    intro = spoken_script.get("intro") or ""
    beats = spoken_script.get("beats") or []
    if not intro or not beats:
        logger.warning("spoken_script for cluster %s missing intro/beats, skipping audio", anchor_cluster_id)
        return None

    chunks = [intro, *beats]
    pcm_chunks = await asyncio.gather(*(synthesize(chunk) for chunk in chunks))
    if any(pcm is None for pcm in pcm_chunks):
        logger.warning("audio synthesis incomplete for cluster %s, skipping", anchor_cluster_id)
        return None

    # Beat offsets are into the *beats* portion of the timeline, i.e.
    # relative to where narration starts — the intro's duration is beat 0's
    # start offset, matching what the Android player highlights against.
    offsets_seconds: list[float] = []
    running_bytes = len(pcm_chunks[0])  # intro
    for pcm in pcm_chunks[1:]:
        offsets_seconds.append(round(running_bytes / BYTES_PER_SECOND, 2))
        running_bytes += len(pcm)

    concatenated = b"".join(pcm_chunks)
    encoded = await _encode_pcm_to_m4a(concatenated)
    if encoded is None:
        return None

    the_hash = script_hash(spoken_script)
    object_name = f"{anchor_cluster_id}-{the_hash}.{AUDIO_FILE_EXTENSION}"
    url = await _upload_object(object_name, encoded, AUDIO_CONTENT_TYPE)
    if url is None:
        return None

    return {
        "audio_url": url,
        "audio_duration_seconds": round(running_bytes / BYTES_PER_SECOND),
        "audio_beat_offsets": offsets_seconds,
        "spoken_script_hash": the_hash,
    }
