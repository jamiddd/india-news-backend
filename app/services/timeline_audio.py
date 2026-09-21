"""Spoken narration audio for the Timeline/Context tab.

Claude writes the spoken script (see timeline_narrative.py's spoken_script
field) — this module's only job is turning that script into a single
playable audio file: synthesize the script with Sarvam AI's Bulbul v3 TTS
(one API call per news beat), stitch the WAVs with a short silence between
them, encode with ffmpeg, and upload to a public Supabase Storage bucket
(same project as editorial_backgrounds.py, different bucket).

Every function here returns None on any failure — missing config, a TTS
error, an upload error — and that must be non-fatal to the surrounding
narrative-generation cycle: a story with no audio renders exactly like a
story generated before this feature existed. See generate_for_chain in
scripts/build_story_timelines.py for how a None here is handled.

Voice and delivery are fixed, not per-story: English (en-IN), the male
speaker "shubh", pace 1.0, temperature 1.0. These were chosen by ear over
many iterations (see sarvam-test/ in the app repo) together with the script
style that timeline_narrative.py asks Claude for — fillers, abbreviation
spelling and the trailing "...." endings in that script are what make this
voice sound right, so change the two together or not at all.

Chunking: the script is voiced in one call per beat rather than one big
call. The intro is voiced together with beat 0 and the closing together
with the last beat, so the host's opening and sign-off flow into the
adjacent beat instead of restarting the intonation. Sarvam's REST API
returns audio only (no timestamps), so every beat except beat 0 has an
EXACT offset (the real start of its own clip); beat 0's offset — where the
spoken intro ends and the beat itself begins — is ESTIMATED from the
intro's share of that first clip's characters.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import logging
import re
import tempfile
import wave
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"
TTS_MODEL = "bulbul:v3"
TTS_SPEAKER = "shubh"
TTS_LANGUAGE = "en-IN"
TTS_PACE = 1.0
# Bulbul v3's documented temperature range goes to 2.0 but the live API
# rejects anything above 1.0 (HTTP 400) — 1.0 is the max and what the
# approved narration style was tuned at.
TTS_TEMPERATURE = 1.0
# Bulbul v3 rejects a single request over 2500 characters.
MAX_CHARS_PER_CALL = 2500
# A chunk this size or larger is split at sentence boundaries before being
# sent, with headroom so the split never lands right on the API limit.
SPLIT_TARGET_CHARS = 2200

# The value spoken_script["version"] must carry for this module to voice it.
# Scripts written for the old Gemini pipeline (style_scene/style_context and
# bracketed [cue] tags) have no version and would have their cue tags read
# aloud, so generate_audio refuses them rather than producing bad audio.
SCRIPT_VERSION = 2

# Sarvam returns 16-bit mono PCM WAV at the sample rate requested below.
PCM_SAMPLE_RATE = 24_000
PCM_SAMPLE_WIDTH = 2
PCM_CHANNELS = 1
BYTES_PER_SECOND = PCM_SAMPLE_RATE * PCM_SAMPLE_WIDTH * PCM_CHANNELS

# Silence between beats (and between the pieces of an oversized beat).
BEAT_GAP_SECONDS = 0.5
SPLIT_GAP_SECONDS = 0.3

AUDIO_CONTENT_TYPE = "audio/mp4"
AUDIO_FILE_EXTENSION = "m4a"


def _configured() -> bool:
    return bool(settings.SARVAM_API_KEY and settings.SUPABASE_URL and settings.SUPABASE_SERVICE_KEY)


def script_hash(spoken_script: dict) -> str:
    """Stable hash of the spoken script's actual content, used to skip
    re-synthesizing audio when nothing changed — see the 06:00 IST daily
    cycle cost concern in scripts/build_story_timelines.py. sort_keys makes
    this independent of dict ordering, which json.dumps does not otherwise
    guarantee across Python versions/callers."""
    canonical = json.dumps(spoken_script, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")


def _with_ending(text: str) -> str:
    """Give a chunk the pause structure the approved narration style relies
    on: a run of dots before the final sentence and a trailing run after it
    (`... head. .... Last sentence. .......`). Without them the voice rushes
    the last word of every clip ("Google" -> "ggl") and the seam between
    beats sounds abrupt. Done here, not asked of the script writer, so the
    pattern is applied identically to every chunk."""
    sentences = _SENTENCE_BREAK.split(text.strip())
    last = sentences[-1]
    head = " ".join(sentences[:-1])
    return f"{head} .... {last} ......." if head else f"{last} ......."


def build_chunks(spoken_script: dict) -> tuple[list[str], float]:
    """Returns (chunks, intro_share): one text chunk per beat, ready to send.

    chunks[0] is the intro followed by beat 0, chunks[-1] ends with the
    closing, and every chunk in between is one beat. intro_share is the
    fraction of chunks[0]'s spoken text taken up by the intro — where beat 0
    starts inside that clip — used to estimate beat 0's offset. Callers must
    have checked that intro and beats are present."""
    intro = (spoken_script.get("intro") or "").strip()
    beats = [b.strip() for b in spoken_script["beats"]]
    closing = (spoken_script.get("closing") or "").strip()
    chunks = list(beats)
    chunks[0] = f"{intro} {chunks[0]}"
    intro_share = min((len(intro) + 1) / len(chunks[0]), 1.0)
    if closing:
        chunks[-1] = f"{chunks[-1]} {closing}"
    return [_with_ending(c) for c in chunks], intro_share


def _split_for_limit(text: str) -> list[str]:
    """Split text that is too long for one Sarvam call into pieces of at most
    SPLIT_TARGET_CHARS, breaking only between sentences. A single sentence
    longer than the target is left whole (and will simply be rejected by the
    API, i.e. treated as a synthesis failure) rather than cut mid-sentence."""
    if len(text) <= SPLIT_TARGET_CHARS:
        return [text]
    sentences = re.split(r"(?<=[.!?])\s+", text)
    parts: list[str] = []
    current = ""
    for sentence in sentences:
        if current and len(current) + 1 + len(sentence) > SPLIT_TARGET_CHARS:
            parts.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        parts.append(current)
    return parts


async def synthesize(text: str, *, attempts: int = 3, timeout: float = 120) -> Optional[bytes]:
    """POST one piece of text to Sarvam TTS, return WAV bytes or None once
    all attempts are exhausted. Same retry-then-None contract as
    llm_gen.call_claude_json rather than raising, since one failed chunk is
    not distinguishable from "TTS unavailable" to the caller — both mean
    "skip audio for this cycle"."""
    if not settings.SARVAM_API_KEY:
        return None
    payload = {
        "text": text,
        "language_code": TTS_LANGUAGE,
        "model": TTS_MODEL,
        "speaker": TTS_SPEAKER,
        "pace": TTS_PACE,
        "temperature": TTS_TEMPERATURE,
        "speech_sample_rate": PCM_SAMPLE_RATE,
        "output_audio_codec": "wav",
        "enable_preprocessing": False,
    }
    for attempt in range(attempts):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    SARVAM_TTS_URL,
                    # Key goes in a header, never the URL: httpx's INFO-level
                    # request log prints the full URL to the journal.
                    headers={"api-subscription-key": settings.SARVAM_API_KEY},
                    json=payload,
                )
            if response.status_code == 429 or response.status_code >= 500:
                # Rate limit or a transient server error — back off and try
                # again; a bare immediate retry re-hits the same window.
                wait_seconds = 10 * (attempt + 1)
                logger.warning(
                    "Sarvam TTS HTTP %s (attempt %s), waiting %ss", response.status_code, attempt + 1, wait_seconds
                )
                await asyncio.sleep(wait_seconds)
                continue
            if response.status_code >= 400:
                # A 4xx other than 429 is a bad request (e.g. text too long,
                # bad param) — retrying the identical payload cannot help.
                # The body is the API's validation message, not a secret.
                logger.warning("Sarvam TTS rejected the request: HTTP %s: %s", response.status_code, response.text[:500])
                return None
            audios = response.json().get("audios") or []
            if not audios:
                raise ValueError("no audios in Sarvam TTS response")
            return base64.b64decode(audios[0])
        except Exception as exc:  # noqa: BLE001 - any failure here just means "no audio this cycle"
            logger.warning("Sarvam TTS attempt %s failed: %s: %s", attempt + 1, type(exc).__name__, exc)
    return None


def _wav_to_pcm(wav_bytes: bytes) -> Optional[bytes]:
    """Raw PCM frames out of a WAV, or None if it isn't the 24kHz/16-bit/mono
    format everything downstream (ffmpeg input, BYTES_PER_SECOND) assumes."""
    try:
        with wave.open(io.BytesIO(wav_bytes)) as w:
            if (w.getframerate(), w.getsampwidth(), w.getnchannels()) != (
                PCM_SAMPLE_RATE, PCM_SAMPLE_WIDTH, PCM_CHANNELS,
            ):
                logger.warning(
                    "unexpected Sarvam WAV format: rate=%s width=%s channels=%s",
                    w.getframerate(), w.getsampwidth(), w.getnchannels(),
                )
                return None
            return w.readframes(w.getnframes())
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not read Sarvam WAV: %s: %s", type(exc).__name__, exc)
        return None


def _silence(seconds: float) -> bytes:
    return b"\x00" * (int(seconds * PCM_SAMPLE_RATE) * PCM_SAMPLE_WIDTH * PCM_CHANNELS)


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
        # Silent in normal operation would be indistinguishable from every
        # other non-fatal skip here, but this one specifically means "the
        # container's env is missing a key" — a misconfiguration, not a
        # transient TTS/network failure — and looked identical to "audio
        # generation failed" with zero explanation before this line existed.
        logger.warning(
            "generate_audio skipped for cluster %s: not configured (SARVAM_API_KEY=%s, "
            "SUPABASE_URL=%s, SUPABASE_SERVICE_KEY=%s)",
            anchor_cluster_id,
            bool(settings.SARVAM_API_KEY), bool(settings.SUPABASE_URL), bool(settings.SUPABASE_SERVICE_KEY),
        )
        return None

    if spoken_script.get("version") != SCRIPT_VERSION:
        logger.warning(
            "spoken_script for cluster %s is not version %s (legacy Gemini-era script?), skipping audio",
            anchor_cluster_id, SCRIPT_VERSION,
        )
        return None
    intro = spoken_script.get("intro") or ""
    beats = spoken_script.get("beats") or []
    if not intro or not beats:
        logger.warning("spoken_script for cluster %s missing intro/beats, skipping audio", anchor_cluster_id)
        return None

    chunks, intro_share = build_chunks(spoken_script)

    # Synthesized one at a time, not concurrently: 10-14 calls per story is
    # ~1 minute of wall-clock, which the nightly cycle can afford, and it
    # keeps well clear of any per-second rate limit on the API.
    chunk_pcm: list[bytes] = []
    for i, chunk in enumerate(chunks):
        pieces = []
        for part in _split_for_limit(chunk):
            wav = await synthesize(part)
            pcm = _wav_to_pcm(wav) if wav is not None else None
            if pcm is None:
                logger.warning("audio synthesis failed for cluster %s (beat %s), skipping", anchor_cluster_id, i)
                return None
            pieces.append(pcm)
        chunk_pcm.append(_silence(SPLIT_GAP_SECONDS).join(pieces))

    # Stitch with a gap between beats and record where each beat's clip
    # starts. Beats 1..n-1 start exactly at their clip's start; beat 0
    # shares its clip with the spoken intro, so its start is estimated by
    # the intro's share of that clip's characters.
    gap = _silence(BEAT_GAP_SECONDS)
    audio = bytearray()
    clip_starts: list[float] = []
    for pcm in chunk_pcm:
        clip_starts.append(len(audio) / BYTES_PER_SECOND)
        audio += pcm
        audio += gap
    first_clip_seconds = len(chunk_pcm[0]) / BYTES_PER_SECOND
    offsets = list(clip_starts)
    offsets[0] = clip_starts[0] + first_clip_seconds * intro_share
    offsets_seconds = [round(o, 2) for o in offsets]

    encoded = await _encode_pcm_to_m4a(bytes(audio))
    if encoded is None:
        return None

    the_hash = script_hash(spoken_script)
    object_name = f"{anchor_cluster_id}-{the_hash}.{AUDIO_FILE_EXTENSION}"
    url = await _upload_object(object_name, encoded, AUDIO_CONTENT_TYPE)
    if url is None:
        return None

    return {
        "audio_url": url,
        "audio_duration_seconds": round(len(audio) / BYTES_PER_SECOND),
        "audio_beat_offsets": offsets_seconds,
        "spoken_script_hash": the_hash,
    }
