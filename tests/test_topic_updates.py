from datetime import datetime, timedelta, timezone

from app.models import TopicDevelopment, TopicFollow
from app.services import topic_updates as tu

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


def test_normalize_topic_key_collapses_case_and_whitespace():
    assert tu.normalize_topic_key("  Elon   Musk ") == "elon musk"
    assert tu.normalize_topic_key("RBI") == "rbi"
    assert tu.normalize_topic_key("") == ""
    assert tu.normalize_topic_key(None) == ""


def test_push_spacing_and_daily_cap_match_story_updates():
    assert tu.may_push(NOW, None, 0)
    assert not tu.may_push(NOW, NOW - timedelta(minutes=119), 0)
    assert tu.may_push(NOW, NOW - timedelta(hours=2), 0)
    assert tu.may_push(NOW, None, tu.DAILY_CAP - 1)
    assert not tu.may_push(NOW, None, tu.DAILY_CAP)


def test_push_copy_distinguishes_new_story_from_new_bullet():
    follow = TopicFollow(id=1, user_id="u1", topic="Elon Musk", topic_key="elon musk")

    new_story = TopicDevelopment(
        topic_key="elon musk", cluster_id=1, headline="Musk announces new venture",
        bullet="Musk announces new venture", source_count=2, kind="new_story",
    )
    title, body = tu._push_copy(follow, new_story)
    assert title == 'New on "Elon Musk"'
    assert body == "Musk announces new venture"

    new_bullet = TopicDevelopment(
        topic_key="elon musk", cluster_id=1, headline="Musk announces new venture",
        bullet="The venture is backed by three investors", source_count=3, kind="new_bullet",
    )
    title, body = tu._push_copy(follow, new_bullet)
    assert title == 'Update on "Elon Musk"'
    assert body == "The venture is backed by three investors"


def test_a_busy_topic_collapses_to_one_pending_development_per_follow():
    """Several clusters can each produce their own TopicDevelopment for the
    same topic in one detection run; send_topic_updates always sends only
    the newest one per follow (see its docstring), which combined with
    MIN_GAP_BETWEEN_PUSHES is what keeps a broad topic to one push per
    spacing window instead of one per matching story."""
    follow = TopicFollow(
        id=1, user_id="u1", topic="cricket", topic_key="cricket",
        created_at=NOW - timedelta(hours=1), last_development_id=None,
    )
    devs = [
        TopicDevelopment(id=10, topic_key="cricket", cluster_id=1, headline="A", bullet="a", kind="new_story", detected_at=NOW - timedelta(minutes=30)),
        TopicDevelopment(id=11, topic_key="cricket", cluster_id=2, headline="B", bullet="b", kind="new_story", detected_at=NOW - timedelta(minutes=20)),
        TopicDevelopment(id=12, topic_key="cricket", cluster_id=1, headline="A", bullet="a2", kind="new_bullet", detected_at=NOW - timedelta(minutes=5)),
    ]
    candidates = [
        d for d in devs
        if d.detected_at >= follow.created_at and d.id > (follow.last_development_id or 0)
    ]
    assert len(candidates) == 3
    assert candidates[-1].id == 12  # newest only, per send_topic_updates
