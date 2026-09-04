"""One reviewer, one sitting, one push.

compose() is pure so the interesting behaviour — what the reviewer is actually
told, and whether they are told anything at all — is testable without FCM.
"""
from app.services.admin_notify import compose


def _task(*, exists=True, status="approved", waiting=False, summary="x"):
    return {"exists": exists, "status": status, "waiting": waiting,
            "summary": summary, "url": "/admin"}


def _tasks(poll, quiz):
    return {"poll": poll, "quiz": quiz}


class TestCompose:
    def test_both_drafts_waiting_is_one_message(self):
        title, body = compose(_tasks(
            _task(status="draft", waiting=True, summary="Should X happen?"),
            _task(status="draft", waiting=True, summary="5 questions (ai)"),
        ))
        assert title == "Poll and quiz drafts ready"
        assert "Poll: Should X happen?" in body
        assert "Quiz: 5 questions (ai)" in body

    def test_only_one_waiting_names_which(self):
        title, body = compose(_tasks(
            _task(status="approved"),
            _task(status="draft", waiting=True, summary="5 questions (ai)"),
        ))
        assert title == "Quiz draft ready to review"
        assert "Poll" not in body

    def test_nothing_waiting_sends_nothing(self):
        """A push that arrives every morning regardless is one the reviewer
        learns to swipe away without reading."""
        assert compose(_tasks(_task(), _task())) is None

    def test_a_missing_draft_is_reported_not_silent(self):
        """Generation failing is more urgent than a draft waiting — silence
        would let the day ship the fallback unnoticed."""
        title, body = compose(_tasks(
            _task(exists=False, status=None),
            _task(status="approved"),
        ))
        assert "missing" in title.lower()
        assert "Poll: not generated" in body

    def test_waiting_and_missing_are_both_surfaced(self):
        title, body = compose(_tasks(
            _task(status="draft", waiting=True, summary="Should X happen?"),
            _task(exists=False, status=None),
        ))
        assert "1 draft to review" in title and "1 missing" in title
        assert "Poll: Should X happen?" in body
        assert "Quiz: not generated" in body

    def test_rejected_is_a_decision_already_made(self):
        """Rejected means the reviewer chose the fallback. Re-notifying would
        be nagging about a settled question."""
        assert compose(_tasks(_task(status="rejected"), _task(status="approved"))) is None
