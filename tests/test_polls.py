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


def test_keeps_sensitive_topics_for_human_review():
    """No keyword blocklist here on purpose: a draft on a hard news topic must
    still be produced so the admin can accept or regenerate it, rather than
    silently collapsing the day's poll to a generic fallback."""
    question, _, _ = validate_draft({
        "question": "Should road-safety funding rise after the rise in highway accident deaths?",
        "context": "Highway safety spending is set annually alongside road construction budgets.",
        "options": ["Increase it", "Keep it unchanged", "Reduce it"],
    })
    assert "accident" in question


def test_accepts_long_sentence_options():
    """Long options are a layout concern, not a failure — the client wraps them,
    and rejecting them only costs us the day's real poll."""
    _, _, options = validate_draft({
        "question": "Should India's growth strategy keep prioritising headline GDP growth?",
        "context": "India retained its position as the fastest-growing large economy at 7.8%.",
        "options": [
            "Prioritise sustaining high GDP growth rates through current economic policies",
            "Shift focus toward reducing inequality and ensuring balanced regional development alongside growth",
        ],
    })
    assert max(len(option) for option in options) > 80


def test_appends_missing_question_mark():
    question, _, _ = validate_draft({
        "question": "Should cities reserve more road space for public transport",
        "context": "The decision can affect congestion, access and travel times.",
        "options": ["Yes", "No"],
    })
    assert question.endswith("?")
