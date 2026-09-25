from datetime import datetime, timedelta, timezone

from app.services import story_updates as su

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)

BASE = [
    "Rescue teams have pulled 12 people from the collapsed building in Mumbai",
    "Police say the builder has been detained for questioning",
]


def test_split_bullets_reads_stored_summary_format():
    assert su.split_bullets("\n• First point\n• Second point") == ["First point", "Second point"]
    assert su.split_bullets(None) == []
    assert su.split_bullets("") == []


def test_paraphrase_is_not_new():
    current = [
        "Twelve people were pulled from the collapsed Mumbai building by rescue teams",
        "The builder has been detained by police for questioning",
    ]
    # Reworded versions of the same two points share most content words.
    assert su.find_new_bullet(current, BASE) is None


def test_restatement_with_same_words_is_not_new():
    assert su.find_new_bullet(list(BASE), BASE) is None


def test_genuinely_new_fact_is_found():
    current = BASE + ["The state government announced a compensation of Rs 5 lakh for each victim's family"]
    assert su.find_new_bullet(current, BASE).startswith("The state government")


def test_least_similar_bullet_wins():
    current = [
        "Police say the builder has been detained and charged with negligence",
        "Court grants bail hearing date to the owner of the adjoining plot",
    ]
    assert su.find_new_bullet(current, BASE).startswith("Court grants")


def test_empty_known_makes_any_bullet_new():
    assert su.find_new_bullet(["Something happened"], []) == "Something happened"


def test_development_needs_new_outlet_and_enrichment_and_bullet():
    ok = dict(enriched_since_check=True, source_count=6, last_source_count=5, new_bullet="x")
    assert su.is_genuine_development(**ok)
    assert not su.is_genuine_development(**{**ok, "source_count": 5})
    assert not su.is_genuine_development(**{**ok, "enriched_since_check": False})
    assert not su.is_genuine_development(**{**ok, "new_bullet": None})


def test_push_spacing_and_daily_cap():
    assert su.may_push(NOW, None, 0)
    assert not su.may_push(NOW, NOW - timedelta(minutes=119), 0)
    assert su.may_push(NOW, NOW - timedelta(hours=2), 0)
    assert su.may_push(NOW, None, su.DAILY_CAP - 1)
    assert not su.may_push(NOW, None, su.DAILY_CAP)


def test_follow_expires_after_72h_quiet():
    followed = NOW - timedelta(days=10)
    assert not su.follow_expired(NOW, NOW - timedelta(hours=71), followed)
    assert su.follow_expired(NOW, NOW - timedelta(hours=72), followed)


def test_recent_follow_of_old_story_is_not_expired_immediately():
    assert not su.follow_expired(NOW, NOW - timedelta(days=9), NOW - timedelta(hours=1))
    assert su.follow_expired(NOW, NOW - timedelta(days=9), NOW - timedelta(hours=72))
