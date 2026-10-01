from datetime import datetime, timezone

from app.services import horoscope_notifications as hn

FORECAST = {
    "sign": "aries",
    "horoscope": {
        "general": "Today favors bold moves at work. A conversation you've "
                    "been putting off finally goes your way, and a small "
                    "financial decision pays off by evening.",
        "career": "x", "finance": "x", "health": "x", "romance": "x",
    },
}


def test_build_title_capitalises_sign():
    assert hn.build_title("aries") == "Aries · Today"
    assert hn.build_title("SCORPIO") == "Scorpio · Today"


def test_build_body_returns_short_text_unchanged():
    forecast = {"horoscope": {"general": "A calm, steady day."}}
    assert hn.build_body(forecast) == "A calm, steady day."


def test_build_body_trims_long_text_at_word_boundary():
    body = hn.build_body(FORECAST, max_len=40)
    assert len(body) <= 41  # trimmed text + ellipsis
    assert body.endswith("…")
    assert not body[:-1].endswith(" ")
    assert FORECAST["horoscope"]["general"].startswith(body[:-1])


def test_build_body_handles_missing_general_text():
    assert hn.build_body({}) == ""
    assert hn.build_body({"horoscope": {}}) == ""


def test_send_window_is_seven_to_eleven_ist():
    # IST is UTC+5:30 — 07:00 IST is 01:30 UTC, 11:00 IST is 05:30 UTC.
    just_before = datetime(2026, 9, 29, 1, 29, tzinfo=timezone.utc)
    at_open = datetime(2026, 9, 29, 1, 30, tzinfo=timezone.utc)
    at_close = datetime(2026, 9, 29, 5, 30, tzinfo=timezone.utc)

    assert not (hn.SEND_FROM <= just_before.astimezone(hn.IST).time() < hn.SEND_UNTIL)
    assert hn.SEND_FROM <= at_open.astimezone(hn.IST).time() < hn.SEND_UNTIL
    assert not (hn.SEND_FROM <= at_close.astimezone(hn.IST).time() < hn.SEND_UNTIL)
