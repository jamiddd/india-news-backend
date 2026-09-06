"""The "All Stories" importance score.

One definition, imported by everything that writes headline_score, so the
poll-cycle recompute and the migration backfill cannot drift apart. They
were duplicated when the age gate was corrected for last_updated_at drift
and this score was not — which is how a story stayed pinned to the top of
Top Headlines for a day and a half. See poll_all_sources() for the full
reasoning behind both the anchor and the log.
"""

# Age is measured from COALESCE(became_multi_source_at, first_seen_at):
# both are written once and never rewritten, unlike last_updated_at, which
# every newly matched article refreshes. The numerator is LN(1 + n) because
# corroboration has diminishing returns — the 60th outlet repeating a story
# adds far less than the 2nd did.
HEADLINE_SCORE_EXPR = """
    LN(1 + distinct_source_count) / POWER(
        GREATEST(EXTRACT(EPOCH FROM (
            now() - COALESCE(became_multi_source_at, first_seen_at)
        )) / 3600.0, 0) + 2,
        1.5
    )
"""

UPDATE_HEADLINE_SCORES_SQL = (
    f"UPDATE story_clusters SET headline_score = {HEADLINE_SCORE_EXPR}"
)


def headline_score(distinct_source_count: int, age_hours: float) -> float:
    """Python mirror of HEADLINE_SCORE_EXPR, for tests and analysis.

    Kept beside the SQL so the two are edited together. Not used at request
    time — the stored column is what the feed paginates against.
    """
    import math

    return math.log(1 + distinct_source_count) / (max(age_hours, 0.0) + 2) ** 1.5
