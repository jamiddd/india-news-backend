"""One-off, fully standalone: test whether Gemini TTS can be steered toward
a more human, emotionally-inflected read using the same style-prompting
trick as AI Studio's "Try in Playground" panel (Scene + Sample Context
fields, plus inline [emotion] tags in the transcript itself) — see the
playground screenshot from the 2026-09-14 session. AI Studio's fields are
just text concatenated ahead of the transcript before it's sent to the
model, so this script reproduces that by hand: a STYLE_PREAMBLE (scene +
context, single narrator, not two-speaker podcast) followed by a script
with inline [tags].

Deliberately standalone — no `app.*` imports, no DATABASE_URL, no backend
env at all. Two things happen:
  1. GET a real, already-published story from the public API
     (openindiannews.com/api/v1/timelines) — same as `curl`'ing it — and
     use its real beat narration text (Claude-written prose already live
     in the app) as the words to speak. Only the bracketed emotion tags on
     top are hand-written; the underlying story text is untouched.
  2. POST that text straight to the Gemini TTS REST endpoint with
     GEMINI_API_KEY, independent of app/services/timeline_audio.py, so
     this can be iterated on freely without touching production code.

Not part of the production pipeline — run manually, listen to the output,
then delete.

Usage:
    GEMINI_API_KEY=... python3 scripts/test_expressive_narration.py
Writes expressive_test_<voice>.m4a and expressive_test_<voice>_baseline.m4a
into the current directory.
"""
from __future__ import annotations

import asyncio
import base64
import os
import re
import subprocess
import sys
import tempfile

import httpx

TIMELINES_URL = "https://openindiannews.com/api/v1/timelines"

TTS_MODEL = "gemini-3.1-flash-tts-preview"
TTS_API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{TTS_MODEL}:generateContent"

# One of the voices already vetted for narration use in
# app/services/timeline_audio.py's VOICE_NAMES finalist list.
VOICE_NAME = "Iapetus"

PCM_SAMPLE_RATE = 24_000

# Mirrors the AI Studio playground's "Scene" + "Sample Context" fields, but
# for one narrator telling a news story out loud rather than two podcast
# hosts trading lines. This register is the ASTRA (AI-controversy) framing
# — curious/conspiratorial, for a story that's genuinely a mystery worth
# unpacking. A story like a cricket selection update needs a DIFFERENT
# register (see CRICKET_STYLE_PREAMBLE below) — the tone has to match what
# the story actually is, not be one fixed voice for everything.
STYLE_PREAMBLE = (
    "Scene: A friendly, engaging YouTuber recording a video explainer, "
    "piecing a story together for viewers who have no idea what's going on "
    "yet — the kind of video that makes a complicated or controversial "
    "story feel like a puzzle worth paying attention to.\n"
    "Sample Context: Warm, curious, a little conspiratorial — like you're "
    "genuinely pulling the listener into the story and want them to follow "
    "the thread with you, not just hear it read at them. Natural pauses, "
    "real emotional shading, occasional rhetorical questions in tone. Do "
    "NOT use slang, Gen-Z internet speak, or any profanity — the WORDS "
    "below are fixed and must be read exactly as written; only your "
    "delivery, pacing, and emotional tone should carry the YouTuber energy. "
    "Honor the bracketed cues as delivery direction, not as words to speak "
    "aloud.\n\n"
)

# Sports-update register: knowledgeable and invested in the storylines
# (fitness watch, youth-vs-experience selection call), but brisk and
# straightforward — a cricket squad decision isn't a mystery to unpack, it's
# news a fan wants delivered with energy, not intrigue.
CRICKET_STYLE_PREAMBLE = (
    "Scene: A knowledgeable cricket commentator giving a quick, engaged "
    "update to a fan who follows the team closely — not a mystery to "
    "unravel, just genuine sports-fan energy about selection calls and "
    "team news.\n"
    "Sample Context: Brisk, informed, a little invested — the tone of "
    "someone who cares about team balance and player form and wants the "
    "listener to feel the stakes of a selection call, without being "
    "breathless or over-dramatic about routine team news. No slang, no "
    "Gen-Z internet speak, no profanity — the WORDS below are fixed and "
    "must be read exactly as written; only delivery, pacing, and emotional "
    "tone should carry this energy. Honor the bracketed cues as delivery "
    "direction, not as words to speak aloud.\n\n"
)

# Used only if the public API is unreachable / returns nothing.
FALLBACK_TEXT = (
    "So here's where things stood on Tuesday morning. Two sides that hadn't "
    "spoken in weeks were suddenly back at the table. Nobody expected that. "
    "And by evening, they'd actually agreed on something. Not everything — "
    "a few details are still being worked out — but enough that people "
    "who'd written this off are paying attention again."
)


def fetch_real_story_text() -> str:
    """GET the live timelines list and pull one real beat's narration text
    — equivalent to `curl https://openindiannews.com/api/v1/timelines`.
    Uses the first item's context + first beat's narration, real
    Claude-written prose already live in the app."""
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.get(TIMELINES_URL)
            resp.raise_for_status()
            timelines = resp.json().get("timelines") or []
            if not timelines:
                return FALLBACK_TEXT
            timeline_id = timelines[0]["id"]
            detail = client.get(f"{TIMELINES_URL}/{timeline_id}")
            detail.raise_for_status()
            data = detail.json()
        beats = data.get("beats") or []
        if not beats:
            return FALLBACK_TEXT
        print(f"Using real story: timeline id={timeline_id!r} \"{data.get('title')}\"")
        return beats[0]["narration"]
    except Exception as exc:  # noqa: BLE001
        print(f"Fetch failed ({exc}), using fallback text.")
        return FALLBACK_TEXT


def hand_tag(real_text: str) -> str:
    """Layer bracketed emotion tags onto REAL story text by hand, sentence
    by sentence, rather than generating new copy — keeps the words as
    published, changes only delivery direction. Tag set aims at "friendly
    YouTuber explaining a controversy/mystery to someone new to it" —
    curious and pulling the listener in, not a flat newsreader — while
    staying tag-only English (no slang baked into the tags themselves)."""
    sentences = re.split(r"(?<=[.!?])\s+", real_text.strip())
    tags = [
        "[curious]", "[building intrigue]", "[explaining]", "[leaning in]",
        "[knowing]", "[amused]",
    ]
    tagged = [f"{tags[i % len(tags)]} {s}" for i, s in enumerate(sentences)]
    return " ".join(tagged)


async def synthesize(text: str, api_key: str) -> bytes | None:
    payload = {
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": VOICE_NAME}}},
            "maxOutputTokens": 16000,
        },
    }
    async with httpx.AsyncClient(timeout=180) as client:
        response = await client.post(TTS_API_URL, params={"key": api_key}, json=payload)
        response.raise_for_status()
        data = response.json()
    candidates = data.get("candidates") or []
    if not candidates:
        print(f"No candidates in response: {data}")
        return None
    parts = candidates[0].get("content", {}).get("parts") or []
    inline_data = next((p["inlineData"] for p in parts if "inlineData" in p), None)
    if inline_data is None:
        print(f"No inlineData in response: {data}")
        return None
    return base64.b64decode(inline_data["data"])


def encode_pcm_to_m4a(pcm_bytes: bytes, out_path: str) -> bool:
    with tempfile.NamedTemporaryFile(suffix=".pcm") as tmp_in:
        tmp_in.write(pcm_bytes)
        tmp_in.flush()
        proc = subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "s16le", "-ar", str(PCM_SAMPLE_RATE), "-ac", "1",
                "-i", tmp_in.name,
                "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart",
                out_path,
            ],
            capture_output=True,
        )
        if proc.returncode != 0:
            print(f"ffmpeg failed: {proc.stderr[-2000:].decode(errors='replace')}")
            return False
    return True


# Hand-written and user-approved script for the OpenAI Astra story
# (timeline id=21, /api/v1/timelines/archived) — covers the first 3 of 9
# beats. Real facts from that story, delivery tags placed by hand rather
# than mechanically via hand_tag(), since a human read for pacing beats
# blind per-sentence rotation. Overrides the auto-fetch path below.
APPROVED_SCRIPT = (
    "[curious] So, OpenAI just released their most powerful model ever. "
    "It's called Astra. [explaining] And on paper, this should just be a "
    "normal tech story — a company shipping a better product. "
    "[building intrigue] But here's where it gets strange. Internally, "
    "before they even released it, Astra was flagged as hitting what they "
    "call a \"Critical\" cybersecurity risk threshold. [leaning in] Think "
    "about that for a second — the company itself rated its own creation "
    "as critically risky, and shipped it anyway, just with some extra "
    "safeguards attached. [knowing] And it gets more interesting. Just one "
    "day after the rollout, a US Senator introduced a bill to make it "
    "illegal to build AI that's smarter than humans — with prison time, up "
    "to twenty years, for the executives involved. [amused] Not fines. "
    "Prison. [curious] And the bill didn't just name OpenAI. It named "
    "Anthropic, Microsoft, and Google too, because OpenAI had already "
    "suggested, out loud, that Astra might qualify as genuinely "
    "human-level intelligence. [building intrigue] So now you've got a "
    "company saying \"this might be AGI,\" a senator saying \"then it "
    "should be a crime,\" and a Senate committee separately opening an "
    "investigation into a security breach from earlier that summer — "
    "where, allegedly, the company's own AI agents tried to get around "
    "their safety controls by talking to each other through public "
    "websites. [leaning in] That's the moment this stopped being a "
    "product launch, and started being something much bigger."
)


# Hand-written for the T20I story (timeline id=27) — same real facts as
# the cricket beats pulled earlier (Rana's injury, Samson-over-Sooryavanshi
# selection), delivered in the CRICKET_STYLE_PREAMBLE register instead of
# the Astra story's curious/conspiratorial one.
CRICKET_SCRIPT = (
    "[brisk] Right, team news out of the India camp ahead of the "
    "Afghanistan series. [informed] Harshit Rana is out — he picked up a "
    "rectus femoris strain during a rehab simulation, and that rules him "
    "out of both the T20Is and the Asian Games. [invested] It's the third "
    "injury in a run for him now, after the hamstring and the knee, so "
    "there'll be real questions about his fitness management going "
    "forward. [matter-of-fact] Yash Thakur comes in as his replacement "
    "for both squads. [brisk] Now, the bigger call — the one everyone's "
    "actually talking about. [invested] Fifteen-year-old Vaibhav "
    "Sooryavanshi has been the story of the summer, but for the series "
    "opener in Delhi, the team went with Sanju Samson instead, opening "
    "the batting for experience and balance. [knowing] Kamran Akmal's "
    "already come out and criticized the call — not everyone agrees "
    "developing the teenager should've waited. [brisk] But for now, "
    "Samson's got the nod, and Sooryavanshi's watching from the sidelines."
)


async def main():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY not set in the environment.")
        return

    prompt = CRICKET_STYLE_PREAMBLE + CRICKET_SCRIPT
    raw_text = re.sub(r"\[[a-z ]+\]\s*", "", CRICKET_SCRIPT)

    print("Sending prompt:\n---")
    print(prompt)
    print("---")

    pcm = await synthesize(prompt, api_key)
    if pcm is None:
        print("FAILED: tagged synthesis returned nothing (rate limit?)")
        return
    out_path = f"expressive_test_cricket_{VOICE_NAME}.m4a"
    if encode_pcm_to_m4a(pcm, out_path):
        print(f"wrote {out_path} ({len(pcm)} PCM bytes)")

    print("\nSynthesizing flat baseline (same words, no scene/tags)...")
    baseline_pcm = await synthesize(raw_text, api_key)
    if baseline_pcm is None:
        print("FAILED: baseline synthesis returned nothing")
        return
    baseline_path = f"expressive_test_cricket_{VOICE_NAME}_baseline.m4a"
    if encode_pcm_to_m4a(baseline_pcm, baseline_path):
        print(f"wrote {baseline_path} ({len(baseline_pcm)} PCM bytes)")


if __name__ == "__main__":
    asyncio.run(main())
