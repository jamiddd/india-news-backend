"""The "All Stories" importance score.

One definition, imported by everything that writes headline_score, so the
poll-cycle recompute and the migration backfill cannot drift apart. They
were duplicated when the age gate was corrected for last_updated_at drift
and this score was not — which is how a story stayed pinned to the top of
Top Headlines for a day and a half. See poll_all_sources() for the full
reasoning behind the anchor.

This one score has to serve two masters — a story leads because it matters,
or because it just happened — and every failure of it so far has been the
two exponents below trading off against each other badly. Tune them with
scripts/eval_ranking.py against live data, never by eye: the harness reports
the median source count and median age of the resulting top 20, which is the
pair of numbers each failure showed up in.

An earlier attempt split the two jobs, adding a separate freshness floor in
GET /clusters so newness did not depend on this score at all. It was
discarded before shipping: a floor lifts everything beneath it to the same
value, so a brand-new 1-source story and a brand-new 2-source one scored
identically, erasing corroboration among exactly the newest stories.
"""
from app.services.feed_gate import LISTING_MAX_AGE

# Age is measured from COALESCE(became_multi_source_at, first_seen_at):
# both are written once and never rewritten, unlike last_updated_at, which
# every newly matched article refreshes.
#
# The two exponents were tuned with scripts/eval_ranking.py against live
# data — run it before changing either, because this score has now been
# wrong in both directions and neither miss was visible by eye:
#
#   n / (h+2)^1.5 anchored on last_updated_at
#       a 60-source story reset its own decay clock and pinned itself to
#       the top of the feed for 36 hours.
#
#   LN(1+n) / (h+2)^1.5 anchored correctly
#       fixed the pinning and overshot. LN compressed 2 -> 40 sources into
#       a 3.4x range while 2.5 hours of ageing cost the same 3.4x, so
#       corroboration stopped mattering: a 40-source story lost to a
#       brand-new 2-source one within 2.5 hours, and the feed filled with
#       minor items minutes old.
#
#   POWER(n, 0.8) / (h+2)
#       right direction, not far enough. Measured on the gated feed it left
#       the top 20 at a median of 2 sources with 65% under an hour old: the
#       one 18-outlet story reached first place and everything under it was
#       still two-source items minutes old.
#
# At 0.9 the breadth range across 2 -> 40 sources is ~14.8x and a 10-source
# story stays ahead of a fresh 2-source one for ~6.5 hours. A power below 1
# still bounds how far breadth can steamroll — the 40th outlet repeating a
# story adds far less than the 2nd did — without flattening it to nothing
# the way LN did.
#
# 1.0 (plain linear counting) surfaces more major stories still, and is not
# the cause of the original pinning: even there a 20-source story 25 hours
# old scores below a brand-new 2-source one, so it ages out on its own. What
# it gives up is the diminishing-returns property above, which is the reason
# to stop at 0.9 rather than go further.
BREADTH_EXPONENT = 0.9

# Gentler than the 1.5 it replaces, which was steep enough that two and a
# half hours of ageing cost as much as the entire 2 -> 40 source range: with
# a numerator that had also been compressed, ageing decided everything.
#
# At 1.0, roughly, a story holds its place against newcomers for as many
# hours as it has outlets. That is the whole trade in one sentence, and it
# is the number to move if the feed reads as too stale (raise it) or too
# churny (lower it).
DECAY_EXPONENT = 1.0

HEADLINE_SCORE_EXPR = f"""
    POWER(distinct_source_count, {BREADTH_EXPONENT}) / POWER(
        GREATEST(EXTRACT(EPOCH FROM (
            now() - COALESCE(became_multi_source_at, first_seen_at)
        )) / 3600.0, 0) + 2,
        {DECAY_EXPONENT}
    )
"""

# headline_score is the leading column of two indexes, so no update to it can
# be a HOT update: every rewritten row writes a new heap tuple, an entry in
# all of the table's indexes, and WAL. The score decays with age, so an
# unconditional UPDATE changed every row on every cycle, even when the value
# moved by a hair. On a Micro instance (1 GB RAM, 11 MB/s baseline disk) that
# exhausted the IO budget on 2026-11-25 and took the project down.
#
# So both statements below only write rows whose score has moved by more than
# a relative threshold. The stored score may lag the exact value by up to the
# threshold, which can swap the order of two near-equal stories and nothing
# more; feeds already tolerate a stale score between polls.
#
# NULL scores always qualify so a freshly inserted row is filled in.
RECENT_SCORE_CHANGE_THRESHOLD = 0.05
# Rows past the recent window are never listed (see below), so the daily sweep
# only needs to keep them roughly right. At 10% a row stops being rewritten
# once its daily relative change drops under 10%, around 10 days old.
FULL_SWEEP_SCORE_CHANGE_THRESHOLD = 0.10


def _changed_by_more_than(threshold: float) -> str:
    return (
        f"(headline_score IS NULL OR ABS(({HEADLINE_SCORE_EXPR}) - headline_score) "
        f"> {threshold} * GREATEST(ABS(headline_score), 1e-9))"
    )


# Clusters older than this are never listed (LISTING_MAX_AGE is days, not
# months), so their score is irrelevant and the sweep leaves them alone rather
# than rewriting hundreds of thousands of rows nobody reads.
FULL_SWEEP_MAX_AGE_DAYS = 10

UPDATE_HEADLINE_SCORES_SQL = (
    f"UPDATE story_clusters SET headline_score = {HEADLINE_SCORE_EXPR} "
    f"WHERE COALESCE(became_multi_source_at, first_seen_at) "
    f"> now() - interval '{FULL_SWEEP_MAX_AGE_DAYS} days' "
    f"AND {_changed_by_more_than(FULL_SWEEP_SCORE_CHANGE_THRESHOLD)}"
)

# Every UPDATE writes a new row version, so rewriting all clusters each poll
# cycle dirtied the whole table and its indexes (~350k rows, ~660 MB) and
# exhausted the database's disk IO budget with almost no traffic. Only
# clusters inside this window can still lead a feed, so the per-cycle
# recompute is limited to them; poller.py runs the unrestricted statement
# above once a day so older rows still decay.
#
# Derived from LISTING_MAX_AGE, which every reader of headline_score filters
# on with this same anchor: nothing older is ever listed, so its score cannot
# affect a feed. One extra day of margin keeps a cluster that is about to age
# out scored right up to the cutoff. Do not hard-code a smaller number.
RECENT_SCORE_WINDOW_DAYS = LISTING_MAX_AGE.days + 1

UPDATE_RECENT_HEADLINE_SCORES_SQL = (
    f"UPDATE story_clusters SET headline_score = {HEADLINE_SCORE_EXPR} "
    f"WHERE COALESCE(became_multi_source_at, first_seen_at) "
    f"> now() - interval '{RECENT_SCORE_WINDOW_DAYS} days' "
    f"AND {_changed_by_more_than(RECENT_SCORE_CHANGE_THRESHOLD)}"
)


def headline_score(distinct_source_count: int, age_hours: float) -> float:
    """Python mirror of HEADLINE_SCORE_EXPR, for tests and analysis.

    Kept beside the SQL so the two are edited together, and read by
    scripts/eval_ranking.py so its "deployed" column always reflects what is
    actually shipped. Not used at request time — the stored column is what
    the feed paginates against.
    """
    return distinct_source_count ** BREADTH_EXPONENT / (max(age_hours, 0.0) + 2) ** DECAY_EXPONENT
