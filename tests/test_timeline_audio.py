from __future__ import annotations

import base64
import io
import json
import wave

import httpx
import pytest

from app.config import settings
from app.services import timeline_audio as ta
from app.services.timeline_narrative import (
    SPOKEN_SCRIPT_ONLY_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    _filler_target,
    finalize_spoken_script,
)


def _wav(seconds: float) -> bytes:
    """A silent 24kHz/16-bit/mono WAV of the given length."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(ta.PCM_CHANNELS)
        w.setsampwidth(ta.PCM_SAMPLE_WIDTH)
        w.setframerate(ta.PCM_SAMPLE_RATE)
        w.writeframes(b"\x00" * int(seconds * ta.BYTES_PER_SECOND))
    return buf.getvalue()


SCRIPT = {
    "intro": "Heyyy everyone, welcome back to Open Indian Voice! So, what happened?",
    "beats": [
        "On the third of September, it began. It grew fast.",
        "The next day, it grew.",
        "Then it ended.",
    ],
    "closing": "That is where it stands. That's the story, right here on Open Indian Voice.",
    "version": ta.SCRIPT_VERSION,
}


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, "SARVAM_API_KEY", "sarvam-test-key", raising=False)
    monkeypatch.setattr(settings, "SUPABASE_URL", "https://proj.supabase.co", raising=False)
    monkeypatch.setattr(settings, "SUPABASE_SERVICE_KEY", "service-key", raising=False)


# --- chunking -------------------------------------------------------------


def test_build_chunks_folds_intro_into_first_and_closing_and_sign_off_into_last():
    chunks, intro_chars = ta.build_chunks(SCRIPT)
    assert len(chunks) == len(SCRIPT["beats"])
    assert chunks[0].startswith("Heyyy everyone, welcome back to Open Indian Voice!")
    assert "On the third of September" in chunks[0]
    assert chunks[1] == "The next day, it grew."
    assert chunks[-1].startswith("Then it ended.")
    assert "right here on Open Indian Voice. " + ta.SIGN_OFF in chunks[-1]
    assert chunks[-1].endswith(ta.SIGN_OFF)
    assert intro_chars == len(SCRIPT["intro"]) + 1


def test_no_chunk_carries_a_dot_pause_marker():
    """Pauses are real silence in the audio; dots in the text made the voice
    speak stray words, so none may ever be sent."""
    chunks, _ = ta.build_chunks(SCRIPT)
    for chunk in chunks:
        assert "..." not in chunk and ". ." not in chunk


def test_build_chunks_single_beat_gets_intro_closing_and_sign_off():
    chunks, _ = ta.build_chunks({**SCRIPT, "beats": ["Only beat."]})
    assert len(chunks) == 1
    assert "Open Indian Voice!" in chunks[0] and "Only beat." in chunks[0]
    assert chunks[0].endswith(ta.SIGN_OFF)


def test_split_last_sentence():
    assert ta.split_last_sentence("One. Two! Three?") == ("One. Two!", "Three?")
    assert ta.split_last_sentence("Only one sentence.") == ("", "Only one sentence.")


def test_split_for_limit_leaves_short_text_alone_and_splits_long_at_sentences():
    assert ta._split_for_limit("Short text.") == ["Short text."]
    sentence = "This is a sentence of moderate length that keeps going. "
    long_text = sentence * 100  # ~5.5k chars
    parts = ta._split_for_limit(long_text)
    assert len(parts) > 1
    assert all(len(p) <= ta.SPLIT_TARGET_CHARS for p in parts)
    assert all(p.endswith(".") for p in parts)


# --- synthesize -----------------------------------------------------------


def _patch_client(monkeypatch, handler):
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        return real(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(ta.httpx, "AsyncClient", factory)


async def test_synthesize_sends_fixed_voice_and_key_in_header(configured, monkeypatch):
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("api-subscription-key")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"audios": [base64.b64encode(_wav(1)).decode()]})

    _patch_client(monkeypatch, handler)
    out = await ta.synthesize("Hello there.")
    assert out == _wav(1)
    assert seen["key"] == "sarvam-test-key"
    assert "sarvam-test-key" not in seen["url"]
    assert seen["body"]["speaker"] == "shubh"
    assert seen["body"]["language_code"] == "en-IN"
    assert seen["body"]["model"] == "bulbul:v3"
    assert seen["body"]["pace"] == 1.0
    assert seen["body"]["temperature"] == 1.0
    assert seen["body"]["output_audio_codec"] == "wav"


async def test_synthesize_does_not_retry_a_bad_request(configured, monkeypatch):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(400, json={"error": {"message": "temperature too high"}})

    _patch_client(monkeypatch, handler)
    assert await ta.synthesize("x") is None
    assert len(calls) == 1


async def test_synthesize_retries_after_rate_limit(configured, monkeypatch):
    calls = []

    async def no_sleep(_):
        return None

    monkeypatch.setattr(ta.asyncio, "sleep", no_sleep)

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, json={})
        return httpx.Response(200, json={"audios": [base64.b64encode(_wav(1)).decode()]})

    _patch_client(monkeypatch, handler)
    assert await ta.synthesize("x") == _wav(1)
    assert len(calls) == 2


async def test_synthesize_without_a_key_is_a_no_op(monkeypatch):
    monkeypatch.setattr(settings, "SARVAM_API_KEY", None, raising=False)
    assert await ta.synthesize("x") is None


# --- generate_audio -------------------------------------------------------


def _stub_pipeline(monkeypatch, durations):
    """Every synthesize() call returns the next duration; encode/upload are
    stubbed so no ffmpeg or network is needed."""
    remaining = list(durations)
    sent = []

    async def fake_synth(text, **kwargs):
        sent.append(text)
        return _wav(remaining.pop(0))

    async def fake_encode(pcm):
        return b"m4a"

    async def fake_upload(name, data, content_type):
        return f"https://cdn.example/{name}"

    monkeypatch.setattr(ta, "synthesize", fake_synth)
    monkeypatch.setattr(ta, "_encode_pcm_to_m4a", fake_encode)
    monkeypatch.setattr(ta, "_upload_object", fake_upload)
    return sent


async def test_generate_audio_returns_one_offset_per_beat_and_uses_real_silence(configured, monkeypatch):
    # Calls, in order: chunk 0 lead-in, chunk 0 last sentence, chunk 1 (one
    # sentence, so a single call), chunk 2 lead-in, chunk 2 last sentence.
    sent = _stub_pipeline(monkeypatch, [30.0, 10.0, 20.0, 25.0, 4.0])
    result = await ta.generate_audio(77, SCRIPT)

    assert len(sent) == 5
    assert all("..." not in text and ". ." not in text for text in sent)  # no dot markers reach the voice
    assert sent[-1] == ta.SIGN_OFF  # the final sentence is voiced on its own

    offsets = result["audio_beat_offsets"]
    assert len(offsets) == len(SCRIPT["beats"])  # the app indexes beats by this
    # chunk 0 = 30s lead-in + 0.5s silence + 10s last sentence, then a 0.9s gap
    assert offsets[1] == pytest.approx(30 + 0.5 + 10 + 0.9)
    # chunk 1 = 20s, then a 0.9s gap
    assert offsets[2] == pytest.approx(30 + 0.5 + 10 + 0.9 + 20 + 0.9)
    # Beat 0 starts partway through the lead-in, after the spoken intro.
    assert 0 < offsets[0] < 30.0
    assert offsets == sorted(offsets)
    # chunk 2 = 25s lead-in + 0.5s silence + 4s last sentence; no trailing gap
    assert result["audio_duration_seconds"] == round(40.5 + 0.9 + 20 + 0.9 + 25 + 0.5 + 4)
    assert result["audio_url"] == f"https://cdn.example/77-{result['spoken_script_hash']}.m4a"
    assert result["spoken_script_hash"] == ta.script_hash(SCRIPT)


async def test_generate_audio_skips_legacy_scripts_with_cue_tags(configured, monkeypatch):
    sent = _stub_pipeline(monkeypatch, [1.0])
    legacy = {
        "style_scene": "A host.",
        "style_context": "Read as written.",
        "intro": "[curious] So what happened?",
        "beats": ["[measured] It began."],
        "closing": "[reflective] That's it.",
    }
    assert await ta.generate_audio(1, legacy) is None
    assert sent == []  # nothing was voiced, so no [cue] was ever read aloud


async def test_generate_audio_not_configured_returns_none(monkeypatch):
    monkeypatch.setattr(settings, "SARVAM_API_KEY", None, raising=False)
    monkeypatch.setattr(settings, "SUPABASE_URL", "https://proj.supabase.co", raising=False)
    monkeypatch.setattr(settings, "SUPABASE_SERVICE_KEY", "service-key", raising=False)
    assert await ta.generate_audio(1, SCRIPT) is None


async def test_generate_audio_fails_whole_story_if_one_beat_fails(configured, monkeypatch):
    calls = []

    async def flaky_synth(text, **kwargs):
        calls.append(text)
        return None if len(calls) == 2 else _wav(1.0)

    monkeypatch.setattr(ta, "synthesize", flaky_synth)
    assert await ta.generate_audio(1, SCRIPT) is None


async def test_generate_audio_rejects_a_script_without_beats(configured, monkeypatch):
    _stub_pipeline(monkeypatch, [])
    assert await ta.generate_audio(1, {**SCRIPT, "beats": []}) is None


# --- script validation + prompt -------------------------------------------


def test_finalize_spoken_script_stamps_the_version():
    script = finalize_spoken_script({"intro": " Hi. ", "beats": ["a", "b"], "closing": "Bye."}, 2)
    assert script == {"intro": "Hi.", "beats": ["a", "b"], "closing": "Bye.", "version": ta.SCRIPT_VERSION}


@pytest.mark.parametrize(
    "bad",
    [
        None,
        "not a dict",
        {"beats": ["a"]},  # no intro
        {"intro": "Hi", "beats": ["a"]},  # wrong beat count for 2
        {"intro": "Hi", "beats": ["a", ""]},  # empty beat
    ],
)
def test_finalize_spoken_script_rejects_bad_scripts(bad):
    assert finalize_spoken_script(bad, 2) is None


def test_prompts_use_the_sarvam_style_and_no_gemini_fields():
    for prompt in (SYSTEM_PROMPT, SPOKEN_SCRIPT_ONLY_SYSTEM_PROMPT):
        assert "<<SPOKEN_SCRIPT_RULES>>" not in prompt  # placeholder was substituted
        assert "Open Indian Voice" in prompt
        assert "style_scene" not in prompt and "style_context" not in prompt
        assert "[curious]" not in prompt


def test_filler_target_scales_with_script_length_and_is_at_least_one():
    assert _filler_target("short", ["tiny"]) == 1
    # ~4,500 written chars becomes a ~5,400-char script -> about eight fillers.
    assert _filler_target("c" * 500, ["b" * 400] * 10) == 8
