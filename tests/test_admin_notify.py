"""One reviewer, one sitting, one push.

compose() is pure so the interesting behaviour — what the reviewer is actually
told, and whether they are told anything at all — is testable without FCM.
"""
from datetime import datetime, timedelta

import pytest

from app.config import settings
from app.services import admin_notify
from app.services.admin_notify import compose, compose_timeline_generation


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


class TestComposeTimelineGeneration:
    def test_nothing_generated_sends_nothing(self):
        """Unchanged chains cost no LLM call and report nothing; a quiet day
        must not become a daily "nothing happened" push. Unfilled slots alone
        don't count — that is the previous state persisting, not news."""
        assert compose_timeline_generation([], rejected=0, failed=0, unfilled=0) is None
        assert compose_timeline_generation([], rejected=0, failed=0, unfilled=3) is None

    def test_generated_titles_are_listed(self):
        title, body = compose_timeline_generation(
            ["Manipur talks", "Monsoon session"], rejected=0, failed=0, unfilled=0)
        assert title == "Timelines: 2 generated"
        assert body == "Manipur talks · Monsoon session"

    def test_failures_and_rejections_are_surfaced_with_the_counts(self):
        title, body = compose_timeline_generation(
            ["Manipur talks"], rejected=2, failed=1, unfilled=1)
        assert title == "Timelines: 1 generated, 1 failed, 2 rejected"
        assert "2 rejected as incoherent" in body
        assert body.endswith("1 slot unfilled")

    def test_only_failures_still_notifies(self):
        """Generation erroring is exactly what the admin needs to hear about,
        even when nothing else was produced."""
        title, body = compose_timeline_generation([], rejected=0, failed=2, unfilled=0)
        assert title == "Timelines: 2 failed"

    def test_long_title_lists_are_capped(self):
        titles = [f"Story {i}" for i in range(7)]
        _, body = compose_timeline_generation(titles, rejected=0, failed=0, unfilled=2)
        assert "Story 4" in body and "Story 5" not in body
        assert "+2 more" in body
        assert "2 slots unfilled" in body


class TestNotifyAdminTimelinesGenerated:
    @pytest.fixture(autouse=True)
    def channels(self, monkeypatch):
        monkeypatch.setattr(settings, "ADMIN_ALERT_TOPIC", "admin-alerts")
        monkeypatch.setattr(settings, "ADMIN_USER_EMAIL", None)
        self.pushes = []
        self.emails = []

        async def push(session, title, body, url):
            self.pushes.append((title, body, url))
            return True

        async def email(subject, body):
            self.emails.append((subject, body))
            return True

        monkeypatch.setattr(admin_notify, "_push_to_admin", push)
        monkeypatch.setattr(admin_notify, "send_admin_email", email)

    async def test_pushes_and_emails_with_a_link_to_the_timelines_page(self):
        result = await admin_notify.notify_admin_timelines_generated(
            None, ["Manipur talks"], rejected=0, failed=0, unfilled=0)
        assert result is True
        assert self.pushes == [(
            "Timelines: 1 generated", "Manipur talks", "https://admin.openindiannews.com/timelines")]
        assert self.emails[0][0] == "Timelines: 1 generated"
        assert self.emails[0][1].endswith("https://admin.openindiannews.com/timelines")

    async def test_nothing_generated_sends_nothing(self):
        result = await admin_notify.notify_admin_timelines_generated(
            None, [], rejected=0, failed=0, unfilled=0)
        assert result is False
        assert self.pushes == [] and self.emails == []

    async def test_never_raises_when_delivery_blows_up(self, monkeypatch):
        async def boom(session, title, body, url):
            raise RuntimeError("db gone")

        monkeypatch.setattr(admin_notify, "_push_to_admin", boom)
        result = await admin_notify.notify_admin_timelines_generated(
            None, ["Manipur talks"], rejected=0, failed=0, unfilled=0)
        assert result is False


class TestPushToAdminTopic:
    """Regression coverage for the exact bug that caused a breaking review to
    go unnoticed: the admin account had zero device tokens (signed-out debug
    install), so _push_to_admin sent nothing and silently returned False."""

    @pytest.fixture(autouse=True)
    def topic(self, monkeypatch):
        monkeypatch.setattr(settings, "ADMIN_ALERT_TOPIC", "admin-alerts")
        monkeypatch.setattr(settings, "ADMIN_USER_EMAIL", None)

    async def test_sends_to_topic_even_with_no_admin_user(self, monkeypatch):
        sent_messages = []
        monkeypatch.setattr(
            "app.services.firebase_auth._get_firebase_app", lambda: "fake-app"
        )
        monkeypatch.setattr(
            "firebase_admin.messaging.send",
            lambda message, app=None: sent_messages.append(message),
        )

        result = await admin_notify._push_to_admin(
            session=None, title="Breaking review: 1 new candidate",
            body="waiting", url="https://admin.openindiannews.com/breaking",
        )

        assert result is True
        assert len(sent_messages) == 1
        assert sent_messages[0].topic == "admin-alerts"
        assert sent_messages[0].data["channel_id"] == "admin_alerts"

    async def test_returns_false_when_topic_send_fails_and_no_admin_user(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.firebase_auth._get_firebase_app", lambda: "fake-app"
        )

        def _raise(message, app=None):
            raise RuntimeError("fcm unreachable")

        monkeypatch.setattr("firebase_admin.messaging.send", _raise)

        result = await admin_notify._push_to_admin(
            session=None, title="t", body="b", url="u",
        )
        assert result is False


class TestNotifyAdminFailure:
    @pytest.fixture(autouse=True)
    def reset_cooldown(self):
        admin_notify._last_failure_alert.clear()
        yield
        admin_notify._last_failure_alert.clear()

    async def test_sends_email_on_first_failure(self, monkeypatch):
        sent = []
        monkeypatch.setattr(
            "app.services.admin_notify.send_admin_email",
            lambda subject, body: sent.append((subject, body)) or _resolved(True),
        )
        result = await admin_notify.notify_admin_failure("breaking_cycle", ValueError("boom"))
        assert result is True
        assert len(sent) == 1
        assert "breaking_cycle" in sent[0][0]

    async def test_suppresses_repeat_within_cooldown(self, monkeypatch):
        sent = []
        monkeypatch.setattr(
            "app.services.admin_notify.send_admin_email",
            lambda subject, body: sent.append((subject, body)) or _resolved(True),
        )
        await admin_notify.notify_admin_failure("breaking_cycle", ValueError("boom"))
        result = await admin_notify.notify_admin_failure("breaking_cycle", ValueError("boom again"))
        assert result is False
        assert len(sent) == 1

    async def test_allows_repeat_after_cooldown_expires(self, monkeypatch):
        sent = []
        monkeypatch.setattr(
            "app.services.admin_notify.send_admin_email",
            lambda subject, body: sent.append((subject, body)) or _resolved(True),
        )
        key = "breaking_cycle:ValueError"
        admin_notify._last_failure_alert[key] = (
            datetime.utcnow() - admin_notify._FAILURE_COOLDOWN - timedelta(seconds=1)
        )
        result = await admin_notify.notify_admin_failure("breaking_cycle", ValueError("boom"))
        assert result is True
        assert len(sent) == 1

    async def test_different_exception_types_are_independent(self, monkeypatch):
        sent = []
        monkeypatch.setattr(
            "app.services.admin_notify.send_admin_email",
            lambda subject, body: sent.append((subject, body)) or _resolved(True),
        )
        await admin_notify.notify_admin_failure("breaking_cycle", ValueError("boom"))
        result = await admin_notify.notify_admin_failure("breaking_cycle", KeyError("other"))
        assert result is True
        assert len(sent) == 2


async def _resolved(value):
    return value
