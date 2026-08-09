from datetime import date

import pytest

from app.services.polls import poll_times, validate_draft


def test_poll_window_is_nine_to_nine_ist():
    publish, closes = poll_times(date(2026, 8, 10))
    assert publish.hour == 9
    assert closes - publish == __import__("datetime").timedelta(days=1)


def test_validates_balanced_poll_shape():
    question, context, options = validate_draft({
        "question": "Should cities reserve more road space for public transport?",
        "context": "The decision can affect congestion, access and travel times.",
        "options": ["Yes", "Only on major routes", "No"],
    })
    assert question.endswith("?")
    assert len(options) == 3


def test_rejects_duplicate_options():
    with pytest.raises(ValueError, match="distinct"):
        validate_draft({
            "question": "Should schools offer more practical financial education?",
            "context": "Students encounter financial decisions after leaving school.",
            "options": ["Yes", "yes"],
        })
