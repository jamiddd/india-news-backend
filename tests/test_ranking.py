"""Behavioural properties of the "All Stories" importance score.

These encode why the formula is shaped the way it is, not just what it
computes. The score has failed in both directions inside one day, so each
failure gets a test that would have caught it:

  * a 60-source story pinned to the top for 36 hours, because the score
    decayed from last_updated_at and every newly matched article reset it;
  * then, after that was fixed, a 40-source story losing to a brand-new
    2-source one within 2.5 hours, because LN(1 + n) compressed breadth to a
    3.4x range while 2.5 hours of ageing cost the same.

See app/services/ranking.py, and scripts/eval_ranking.py for checking a
change against live data before it ships.
"""

from app.services.explore_bandit import EXPLORE_PROMOTED_BOOST
from app.services.ranking import (
    BREADTH_EXPONENT,
    DECAY_EXPONENT,
    HEADLINE_SCORE_EXPR,
    headline_score,
)


class TestTheTwoFailures:
    def test_a_big_day_old_story_loses_to_a_fresh_mid_size_one(self):
        """Failure 1: the pinning bug, stated as a test.

        Age is real elapsed time since the story broke, so a huge story from
        yesterday must yield to a fresh, smaller one. Under the original
        formula the big story's clock was reset by every incoming article and
        this comparison went the other way indefinitely.
        """
        nepal_36h_old_60_sources = headline_score(60, 36)
        fresh_5_source_story = headline_score(5, 1)

        assert fresh_5_source_story > nepal_36h_old_60_sources

    def test_a_well_corroborated_story_survives_the_morning(self):
        """Failure 2: the burial regression, stated as a test.

        A story ten outlets are covering must not be swept off the feed by a
        two-source item that happens to have just arrived. Under LN(1 + n) it
        lost this after 1.4 hours, which is what filled the feed with minor
        items minutes old.
        """
        ten_sources_four_hours_old = headline_score(10, 4)
        brand_new_two_source_item = headline_score(2, 0)

        assert ten_sources_four_hours_old > brand_new_two_source_item

    def test_a_major_story_survives_the_working_day(self):
        assert headline_score(40, 12) > headline_score(2, 0)


class TestShape:
    def test_breadth_wins_when_age_is_equal(self):
        assert headline_score(60, 2) > headline_score(5, 2) > headline_score(1, 2)

    def test_breadth_has_diminishing_returns(self):
        """The 40th outlet repeating a story says less than the 2nd did.

        A sub-linear exponent is what bounds how far breadth can steamroll —
        the property LN was reached for, without LN's flattening.
        """
        assert BREADTH_EXPONENT < 1.0

        second_outlet = headline_score(2, 2) - headline_score(1, 2)
        fortieth_outlet = headline_score(40, 2) - headline_score(39, 2)

        assert second_outlet > fortieth_outlet

    def test_breadth_still_spans_a_meaningful_range(self):
        """The regression in one number.

        At LN this ratio was 3.4x, less than 2.5 hours of ageing cost, so
        corroboration stopped deciding anything. It does not need to be huge —
        the freshness term in GET /clusters carries new stories — but it has to
        be worth more than an afternoon.
        """
        ratio = headline_score(40, 2) / headline_score(2, 2)

        assert 8.0 < ratio < 16.0

    def test_score_decays_monotonically_with_age(self):
        scores = [headline_score(10, h) for h in (0, 1, 6, 24, 72)]

        assert scores == sorted(scores, reverse=True)

    def test_nothing_divides_by_zero_at_the_moment_a_story_breaks(self):
        assert headline_score(1, 0) > 0
        # Clock skew between the poller and the database must not produce a
        # negative age, which at a fractional exponent is a complex number.
        assert headline_score(3, -5) == headline_score(3, 0)


class TestCalibration:
    def test_explore_boost_beats_several_sources_but_not_an_enormous_story(self):
        """EXPLORE_PROMOTED_BOOST is tuned against this score's scale.

        It is multiplicative, so it survives a change to the score
        arithmetically while silently ceasing to mean what it was set to mean:
        8.0 was right against a linear numerator, 2.0 against LN, and each was
        wrong for the other. If this fails, re-solve the constant in
        explore_bandit.py rather than relaxing the test.
        """
        promoted_2_source = headline_score(2, 2) * EXPLORE_PROMOTED_BOOST

        assert promoted_2_source > headline_score(8, 2)
        assert promoted_2_source < headline_score(100, 2)

    def test_a_single_source_story_can_never_trigger_a_breaking_alert(self):
        """The push threshold is calibrated against this score's scale too.

        scripts/send_notifications.py compares headline_score against a bare
        float, so a change to the score does not break it — it silently
        changes who gets woken up. The invariant that must survive any
        recalibration is that no 1-outlet story, however fresh, can cross it:
        a push is the most intrusive surface the app has, and an uncorroborated
        report is exactly what should not use it.
        """
        from scripts.send_notifications import BREAKING_SCORE_THRESHOLD

        highest_a_singleton_can_ever_score = headline_score(1, 0)

        assert highest_a_singleton_can_ever_score < BREAKING_SCORE_THRESHOLD

    def test_a_corroborated_breaking_story_can_still_trigger_one(self):
        """The other half: a threshold safely above singletons is useless if it
        is also above everything else."""
        from scripts.send_notifications import BREAKING_SCORE_THRESHOLD

        assert headline_score(3, 0.5) > BREAKING_SCORE_THRESHOLD
        assert headline_score(10, 4) > BREAKING_SCORE_THRESHOLD


class TestSqlAndPythonAgree:
    def test_the_mirror_states_the_same_formula(self):
        """The Python mirror is only useful if both halves say the same thing.

        scripts/eval_ranking.py ranks with the Python side and reports it as
        what is deployed, so a drift here would make the harness confidently
        measure a formula nobody is running.
        """
        assert f"POWER(distinct_source_count, {BREADTH_EXPONENT})" in HEADLINE_SCORE_EXPR
        assert f"{DECAY_EXPONENT}\n" in HEADLINE_SCORE_EXPR

    def test_age_is_anchored_on_a_write_once_column(self):
        """The pinning bug was exactly this line being wrong."""
        assert "COALESCE(became_multi_source_at, first_seen_at)" in HEADLINE_SCORE_EXPR
        assert "last_updated_at" not in HEADLINE_SCORE_EXPR
