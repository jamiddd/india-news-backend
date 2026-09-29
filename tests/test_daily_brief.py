from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.services.daily_brief import served_kind
from app.services.daily_brief_script import NIGHT_SIGN_OFF, _system_prompt, _user_content, build_chunks, finalize_script
from app.services.daily_brief_select import BriefStory, _entity_set, _same_event, brief_window, is_runaway_cluster
from app.services.timeline_audio import SCRIPT_VERSION, SIGN_OFF

IST = ZoneInfo("Asia/Kolkata")


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


def test_wrapup_window_is_todays_own_day_up_to_the_cutoff():
    start, end = brief_window(date(2026, 9, 25), "wrapup")
    assert start.isoformat() == "2026-09-25T00:00:00+05:30"
    assert end.isoformat() == "2026-09-25T19:00:00+05:30"


def test_wrapup_prompt_and_user_content_say_today_not_yesterday():
    brief_prompt = _system_prompt("brief")
    wrapup_prompt = _system_prompt("wrapup")
    assert "yesterday" in brief_prompt and "Daily Brief" in brief_prompt
    assert "today" in wrapup_prompt and "Late-Night Wrap-up" in wrapup_prompt
    assert "yesterday" not in wrapup_prompt

    stories = _stories(1)
    assert _user_content(stories, "brief").startswith("Yesterday's stories")
    assert _user_content(stories, "wrapup").startswith("Today's stories")


def test_wrapup_chunks_end_with_the_night_sign_off():
    stories = _stories()
    script = finalize_script(_script(stories), stories)
    chunks, _ = build_chunks(script, "wrapup")
    assert chunks[-1].endswith(NIGHT_SIGN_OFF)
    assert SIGN_OFF not in chunks[-1]


def test_served_kind_switches_at_night_boundaries():
    tz = lambda h, m=0: datetime(2026, 9, 25, h, m, tzinfo=IST)
    assert served_kind(tz(4, 59)) == "wrapup"
    assert served_kind(tz(5, 0)) == "brief"
    assert served_kind(tz(19, 59)) == "brief"
    assert served_kind(tz(20, 0)) == "wrapup"
    assert served_kind(tz(23, 59)) == "wrapup"
    assert served_kind(tz(0, 0)) == "wrapup"


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


def test_runaway_clusters_are_recognised_by_articles_per_outlet():
    # Healthy stories seen in production: about one to two articles per outlet.
    assert not is_runaway_cluster(34, 26)
    assert not is_runaway_cluster(49, 24)
    assert not is_runaway_cluster(12, 12)
    # Runaway merges: hundreds or thousands of articles.
    assert is_runaway_cluster(1721, 81)
    assert is_runaway_cluster(420, 59)
    assert is_runaway_cluster(258, 42)
    # Missing counts must not flag a story.
    assert not is_runaway_cluster(None, None)
