"""Behavioural properties of the "All Stories" importance score.

These encode why the formula changed, not just what it computes: a
60-source story sat at the top of Top Headlines for a day and a half
because the score decayed from last_updated_at, which every newly matched
article refreshed. See app/services/ranking.py.
"""

from app.services.explore_bandit import EXPLORE_PROMOTED_BOOST
from app.services.ranking import HEADLINE_SCORE_EXPR, headline_score


def test_a_big_day_old_story_loses_to_a_fresh_mid_size_one():
    """The bug, stated as a test.

    Age is now real elapsed time since the story broke, so a huge story
    from yesterday must yield to a fresh, smaller one. Under the old
    formula the big story's clock was reset by every incoming article and
    this comparison went the other way indefinitely.
    """
    nepal_36h_old_60_sources = headline_score(60, 36)
    fresh_5_source_story = headline_score(5, 1)

    assert fresh_5_source_story > nepal_36h_old_60_sources


def test_breadth_still_wins_when_age_is_equal():
    """Compressing the numerator must not neutralize corroboration."""
    assert headline_score(60, 2) > headline_score(5, 2) > headline_score(1, 2)


def test_log_compression_bounds_how_far_breadth_can_steamroll():
    """60 sources beat 5 by ~2.3x, not the ~12x linear counting gave."""
    ratio = headline_score(60, 2) / headline_score(5, 2)

    assert 2.0 < ratio < 2.6


def test_score_decays_monotonically_with_age():
    scores = [headline_score(10, h) for h in (0, 1, 6, 24, 72)]

    assert scores == sorted(scores, reverse=True)


def test_explore_boost_beats_several_sources_but_not_an_enormous_story():
    """EXPLORE_PROMOTED_BOOST is tuned against this score's scale.

    It was 8.0 against a linear numerator. Left there after the switch to
    LN it would have let any promoted story outrank one with thousands of
    outlets, turning the explore slot into a permanent top-of-feed override.
    """
    promoted_2_source = headline_score(2, 2) * EXPLORE_PROMOTED_BOOST

    assert promoted_2_source > headline_score(6, 2)
    assert promoted_2_source < headline_score(100, 2)


def test_sql_and_python_definitions_stay_together():
    """The mirror is only useful if both halves say the same thing."""
    assert "LN(1 + distinct_source_count)" in HEADLINE_SCORE_EXPR
    assert "COALESCE(became_multi_source_at, first_seen_at)" in HEADLINE_SCORE_EXPR
    assert "last_updated_at" not in HEADLINE_SCORE_EXPR
