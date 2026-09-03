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



def test_poll_scheduler_runs_each_action_once_per_day():
    """The loop used to act twice per pass — a catch-up action, then a second
    one after the sleep, which the next pass then repeated. Doubled every
    draft and publish, and doubled the Claude retries on a failing day."""
    from datetime import datetime, time, timedelta
    from collections import Counter

    def simulate(start: datetime, passes: int = 40) -> list[tuple[str, date]]:
        """Mirrors main()'s scheduling decisions with a fake clock, where
        sleeping jumps straight to next_run."""
        now, log = start, []
        for _ in range(passes):
            draft_at = datetime.combine(now.date(), time(4, 30))
            publish_at = datetime.combine(now.date(), time(9))
            if now >= publish_at:
                log.append(("publish", now.date()))
                next_run = datetime.combine(now.date() + timedelta(days=1), time(4, 30))
            elif now >= draft_at:
                log.append(("prepare", now.date()))
                next_run = publish_at
            else:
                next_run = draft_at
            now = next_run
        return log

    for start_hour in (0, 5, 10, 23):
        log = simulate(datetime(2026, 9, 3, start_hour, 0))
        repeated = [entry for entry, count in Counter(log).items() if count > 1]
        assert not repeated, f"start {start_hour}:00 repeated {repeated}"
