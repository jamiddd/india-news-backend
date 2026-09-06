"""The "All Stories" importance score.

One definition, imported by everything that writes headline_score, so the
poll-cycle recompute and the migration backfill cannot drift apart. They
were duplicated when the age gate was corrected for last_updated_at drift
and this score was not — which is how a story stayed pinned to the top of
Top Headlines for a day and a half. See poll_all_sources() for the full
reasoning behind the anchor.

This score answers "how much does this story matter", and only that. It is
deliberately NOT also responsible for putting new stories in front of
readers: GET /clusters blends it with a separate freshness term (see
FRESHNESS_* there), so a brand-new story earns its place on its newness
rather than by the importance score decaying fast enough to fake it. That
split is why the decay here can be gentle without new stories disappearing.
"""

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
# At 0.8 the breadth range across 2 -> 40 sources is ~11x, and a 10-source
# story stays ahead of a fresh 2-source one for ~5 hours rather than 1.4.
# A power below 1 still bounds how far breadth can steamroll — the 40th
# outlet repeating a story adds far less than the 2nd did — without
# flattening it to nothing the way LN did.
BREADTH_EXPONENT = 0.8

# Gentler than the 1.5 it replaces. Recency pressure now comes from the
# freshness term in GET /clusters, so this no longer has to be steep enough
# to push yesterday's news down on its own — which is what made it steep
# enough to push this morning's important news down too.
DECAY_EXPONENT = 1.0

HEADLINE_SCORE_EXPR = f"""
    POWER(distinct_source_count, {BREADTH_EXPONENT}) / POWER(
        GREATEST(EXTRACT(EPOCH FROM (
            now() - COALESCE(became_multi_source_at, first_seen_at)
        )) / 3600.0, 0) + 2,
        {DECAY_EXPONENT}
    )
"""

UPDATE_HEADLINE_SCORES_SQL = (
    f"UPDATE story_clusters SET headline_score = {HEADLINE_SCORE_EXPR}"
)


def headline_score(distinct_source_count: int, age_hours: float) -> float:
    """Python mirror of HEADLINE_SCORE_EXPR, for tests and analysis.

    Kept beside the SQL so the two are edited together, and read by
    scripts/eval_ranking.py so its "deployed" column always reflects what is
    actually shipped. Not used at request time — the stored column is what
    the feed paginates against.
    """
    return distinct_source_count ** BREADTH_EXPONENT / (max(age_hours, 0.0) + 2) ** DECAY_EXPONENT
