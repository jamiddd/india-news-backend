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

Chunks (intro, one per beat, closing) are batched into as few TTS calls as
fit under a per-call duration budget (see GROUP_MAX_SECONDS) rather than
one call per chunk — Gemini's daily request quota (100 RPD observed on
this account 2026-09-14) is the binding constraint in production, not
tokens or cost, and an 8-10-chunk story at one-call-per-chunk could burn
most of a day's quota by itself. There's no word-level timestamp API, so
a chunk sharing a group with others gets its beat offset ESTIMATED by its
share of the group's character count (see generate_audio) rather than
measured exactly — only a chunk alone in its own group keeps an exact
offset. This trades perfect sync for staying well under the daily quota;
speech rate is steady enough sentence-to-sentence that it should still
track closely for a highlighting feature.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import random
import tempfile
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# 3.1 Flash costs 2x 2.5 Flash ($1.00/$20.00 vs $0.50/$10.00 per 1M input/
# output tokens, per ai.google.dev/gemini-api/docs/pricing checked
# 2026-09-14) — same price tier as 2.5 Pro, not a cheaper Flash step. Picked
# deliberately anyway for better quality; revisit if monthly TTS spend
# becomes a real line item.
TTS_MODEL = "gemini-3.1-flash-tts-preview"

# Narrowed 2026-09-15 from the original 5-voice finalist pool (Iapetus,
# Aoede, Algenib, Gacrux, Sadachbia — picked by ear from a full 30-voice
# Gemini TTS sample run, see scripts/test_voice_samples.py) down to these 2,
# after the first 3 real narrations all landed on similar-sounding voices
# from that pool. One is chosen at random per story (see generate_audio)
# rather than per chunk, so a single narration stays one consistent voice
# throughout while different stories in the Timeline tab still get some
# variety instead of everything sounding identical.
VOICE_NAMES = ["Algenib", "Gacrux"]

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

# Gemini's maxOutputTokens=16000 for audio caps a single TTS call at
# 16000/25 = 640s of speech. Grouping multiple chunks (intro/beats/closing)
# into one call — instead of one call per chunk — is what keeps a typical
# 8-10-chunk story's TTS cost down to 1-2 requests instead of 8-10,
# which matters because Gemini's daily request quota (RPD) is the binding
# constraint observed in production (100 RPD on this account as of
# 2026-09-14 — see aistudio.google.com/rate-limit), not tokens or cost.
# This budget is set well under the 640s hard cap (not right up against
# it) so a single long beat doesn't push a group over on its own.
GROUP_MAX_SECONDS = 480
# ~150 wpm spoken English, ~5.1 chars/word incl. space -> ~12.75 chars/sec.
# Used only to decide how many chunks fit in a group BEFORE synthesizing
# (we don't know real audio duration until Gemini returns it) — a rough
# per-group ceiling, not a per-beat offset estimate (see the char-
# proportion note in generate_audio below, which is a separate estimate
# computed from the ACTUAL group duration after synthesis).
ESTIMATED_CHARS_PER_SECOND = 12.5


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


async def synthesize(
    text: str, *, voice_name: str = VOICE_NAMES[0], attempts: int = 3, timeout: float = 240
) -> Optional[bytes]:
    """POST one chunk of text to Gemini TTS, return raw PCM bytes or None
    once all attempts are exhausted. Mirrors llm_gen.call_claude_json's
    retry-then-None contract rather than raising, since a single failed
    chunk should not be distinguishable from "TTS unavailable" to the
    caller — both mean "skip audio for this cycle".

    timeout default was 60s originally, which turned out too short once
    groups are batched up toward GROUP_MAX_SECONDS=480s of speech — a
    2026-09-15 backfill run hit ReadTimeout on every row at 60s (confirmed
    the group itself was fine; a local one-off test of a ~90s-of-speech
    chunk needed ~180s of wall-clock generation time). 240s leaves
    headroom without being open-ended."""
    if not settings.GEMINI_API_KEY:
        return None
    payload = {
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice_name}}
            },
            # Audio output is token-metered same as text, and the default
            # cap is well short of what a multi-sentence beat needs — a
            # short chunk was observed cutting off mid-sentence with no
            # config here at all. Generous headroom; a beat/intro chunk is
            # at most a few hundred words.
            "maxOutputTokens": 16000,
        },
    }
    for attempt in range(attempts):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    TTS_API_URL,
                    # Key goes in a header, not a URL query param — httpx's
                    # INFO-level request log prints the full URL (including
                    # query string) to the journal on every call, which was
                    # leaking this key into plaintext logs. Headers aren't
                    # logged, so this stops that.
                    headers={"x-goog-api-key": settings.GEMINI_API_KEY},
                    json=payload,
                )
                if response.status_code == 429:
                    # Preview TTS models carry tight per-minute quotas even
                    # on paid tiers — a bare retry with no delay just
                    # re-hits the same window and fails identically three
                    # times in a row. Back off long enough to plausibly land
                    # in the next quota window instead.
                    wait_seconds = 20 * (attempt + 1)
                    logger.warning("Gemini TTS rate limited (attempt %s), waiting %ss", attempt + 1, wait_seconds)
                    await asyncio.sleep(wait_seconds)
                    continue
                response.raise_for_status()
                data = response.json()
            candidates = data.get("candidates") or []
            if not candidates:
                raise ValueError(f"No candidates in Gemini TTS response: {data!r}"[:2000])
            finish_reason = candidates[0].get("finishReason")
            if finish_reason and finish_reason not in ("STOP", "FINISH_REASON_UNSPECIFIED"):
                # MAX_TOKENS here means the audio itself was cut off mid-
                # speech, not just a text-generation quirk — surface it
                # loudly since it produces a file that looks structurally
                # valid (ffprobe parses fine) but is missing audio content.
                raise ValueError(
                    f"Gemini TTS stopped early: finishReason={finish_reason}; "
                    f"usage={data.get('usageMetadata')}"
                )
            parts = candidates[0].get("content", {}).get("parts") or []
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
        # Silent in normal operation would be indistinguishable from every
        # other non-fatal skip here, but this one specifically means "the
        # container's env is missing a key" — a misconfiguration, not a
        # transient TTS/network failure — and 2026-09-15 lost a whole
        # backfill run's worth of diagnosis to it looking identical to
        # "audio generation failed" with zero explanation. Worth the one
        # log line even though every other failure path in this function
        # stays quiet by design.
        logger.warning(
            "generate_audio skipped for cluster %s: not configured (GEMINI_API_KEY=%s, "
            "SUPABASE_URL=%s, SUPABASE_SERVICE_KEY=%s)",
            anchor_cluster_id,
            bool(settings.GEMINI_API_KEY), bool(settings.SUPABASE_URL), bool(settings.SUPABASE_SERVICE_KEY),
        )
        return None

    intro = spoken_script.get("intro") or ""
    beats = spoken_script.get("beats") or []
    closing = spoken_script.get("closing") or ""
    if not intro or not beats:
        logger.warning("spoken_script for cluster %s missing intro/beats, skipping audio", anchor_cluster_id)
        return None

    # closing is optional (older cached spoken_scripts predate it) — it's
    # just one more chunk appended after the beats.
    chunks = [intro, *beats, *([closing] if closing else [])]

    # style_scene/style_context (see timeline_narrative.py's SYSTEM_PROMPT)
    # are Claude's per-story delivery direction — same trick as AI Studio's
    # "Try in Playground" Scene/Sample Context fields, chosen per story
    # rather than one fixed register for everything (a cricket selection
    # call and an AI-safety controversy shouldn't be read the same way).
    # Older cached spoken_scripts predate this — absent means "no style
    # direction", not an error, so this stays optional exactly like closing.
    style_scene = spoken_script.get("style_scene") or ""
    style_context = spoken_script.get("style_context") or ""
    style_preamble = f"Scene: {style_scene}\nSample Context: {style_context}\n\n" if style_scene and style_context else ""

    # Group consecutive chunks into as few TTS calls as safely fit under
    # GROUP_MAX_SECONDS (estimated from character count, since we don't
    # know real audio duration until Gemini returns it) — this is what
    # turns an 8-10-chunk story into 1-2 requests instead of 8-10. A
    # single chunk longer than the estimated budget still gets its own
    # group rather than being split mid-sentence.
    groups: list[list[int]] = []  # each entry: list of chunk indices
    current_group: list[int] = []
    current_est_seconds = 0.0
    for i, chunk in enumerate(chunks):
        est_seconds = len(chunk) / ESTIMATED_CHARS_PER_SECOND
        if current_group and current_est_seconds + est_seconds > GROUP_MAX_SECONDS:
            groups.append(current_group)
            current_group = []
            current_est_seconds = 0.0
        current_group.append(i)
        current_est_seconds += est_seconds
    if current_group:
        groups.append(current_group)

    # One voice per story, not per chunk/group — picking randomly per call
    # would make a single narration switch voices mid-story.
    voice_name = random.choice(VOICE_NAMES)
    # Nothing else logs which voice a story got — a 2026-09-15 backfill of
    # 3 rows all landing on male-sounding voices was undiagnosable after
    # the fact without this, since object names embed the script hash, not
    # the voice.
    logger.info("cluster %s: picked voice %s", anchor_cluster_id, voice_name)
    # style_preamble is prepended to EVERY group's call, not just the first
    # — each group is an independent, stateless TTS request, so delivery
    # direction has to travel with each one to stay consistent across a
    # multi-group story. The model is instructed not to voice it, so it
    # doesn't affect returned audio duration/offsets below.
    group_texts = [style_preamble + "\n\n".join(chunks[i] for i in group) for group in groups]
    group_pcm = await asyncio.gather(*(synthesize(text, voice_name=voice_name) for text in group_texts))
    if any(pcm is None for pcm in group_pcm):
        logger.warning("audio synthesis incomplete for cluster %s, skipping", anchor_cluster_id)
        return None

    # Per-chunk offset within its group: estimated by that chunk's share
    # of the group's TOTAL CHARACTER COUNT, scaled against the group's
    # ACTUAL audio duration (not the pre-synthesis estimate above). This
    # is an approximation — Gemini TTS has no word-level timestamp API —
    # but speech rate is roughly steady sentence-to-sentence, so it should
    # track closely enough for a highlighting feature (not karaoke-exact
    # sync). Chunks sharing a group with others lose exact-boundary
    # accuracy; a chunk alone in its own group is still exact (its offset
    # is the group's real start, same as before batching).
    chunk_offsets_seconds: list[float] = [0.0] * len(chunks)
    running_bytes = 0
    for group, pcm in zip(groups, group_pcm):
        group_duration = len(pcm) / BYTES_PER_SECOND
        group_start = running_bytes / BYTES_PER_SECOND
        group_chars = sum(len(chunks[i]) for i in group)
        chars_before = 0
        for i in group:
            chunk_offsets_seconds[i] = round(
                group_start + (chars_before / group_chars) * group_duration if group_chars else group_start, 2
            )
            chars_before += len(chunks[i])
        running_bytes += len(pcm)

    # Beat offsets are into the *beats* portion of the timeline, i.e.
    # relative to where narration starts — chunks[0] is the intro,
    # chunks[1:1+len(beats)] are the beats. Only slice the beat chunks
    # here — offsets_seconds must stay exactly len(beats) long, or
    # Android's beat-index lookup (beatIndexForPosition) misaligns.
    offsets_seconds = chunk_offsets_seconds[1 : 1 + len(beats)]

    concatenated = b"".join(group_pcm)
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
