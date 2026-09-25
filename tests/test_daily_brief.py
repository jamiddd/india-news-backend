from datetime import date

from app.services.daily_brief_script import build_chunks, finalize_script
from app.services.daily_brief_select import BriefStory, _entity_set, _same_event, brief_window
from app.services.timeline_audio import SCRIPT_VERSION, SIGN_OFF


def _stories(n=3):
    return [
        BriefStory(cluster_id=10 + i, headline=f"Headline {i}", category="national", source_count=5, image_url=None)
        for i in range(n)
    ]


def _script(stories, **overrides):
    raw = {
        "intro": "Good morning, and welcome to your Daily Brief on Open Indian Voice!",
        "items": [
            {"cluster_id": s.cluster_id, "summary": f"Summary {s.cluster_id}.", "spoken": f"Spoken text for story {chr(97 + s.cluster_id % 26)}."}
            for s in stories
        ],
        "closing": "That's your brief, from Open Indian Voice.",
    }
    raw.update(overrides)
    return raw


def test_window_is_the_previous_ist_day():
    start, end = brief_window(date(2026, 9, 25))
    assert start.isoformat() == "2026-09-24T00:00:00+05:30"
    assert end.isoformat() == "2026-09-25T00:00:00+05:30"


def test_same_event_uses_entity_overlap():
    a = _entity_set({"persons": ["Narendra Modi"], "organizations": ["BJP"], "locations": []})
    b = _entity_set({"persons": ["narendra modi"], "organizations": ["Congress"], "locations": []})
    c = _entity_set({"persons": ["Virat Kohli"], "organizations": ["BCCI"], "locations": []})
    assert _same_event(a, b)  # half of the smaller set is shared
    assert not _same_event(a, c)
    assert not _same_event(a, set())  # no entities -> never treated as duplicate


def test_finalize_accepts_a_matching_script_and_stamps_version():
    stories = _stories()
    script = finalize_script(_script(stories), stories)
    assert script is not None
    assert script["version"] == SCRIPT_VERSION
    assert [i["cluster_id"] for i in script["items"]] == [s.cluster_id for s in stories]


def test_finalize_rejects_wrong_ids_order_or_count():
    stories = _stories()
    raw = _script(stories)
    raw["items"][0], raw["items"][1] = raw["items"][1], raw["items"][0]
    assert finalize_script(raw, stories) is None
    assert finalize_script(_script(stories[:2]), stories) is None


def test_finalize_rejects_digits_and_markup_in_spoken_text():
    stories = _stories()
    raw = _script(stories)
    raw["items"][1]["spoken"] = "The R B I holds the repo rate at 6.5 percent."
    assert finalize_script(raw, stories) is None
    assert finalize_script(_script(stories, intro="Hello [pause] there"), stories) is None


def test_build_chunks_puts_intro_first_and_closing_with_sign_off_last():
    stories = _stories()
    script = finalize_script(_script(stories), stories)
    chunks, intro_chars = build_chunks(script)
    assert len(chunks) == len(stories)
    assert chunks[0].startswith(script["intro"])
    assert intro_chars == len(script["intro"]) + 1
    assert chunks[-1].endswith(SIGN_OFF)
    assert script["closing"] in chunks[-1]
