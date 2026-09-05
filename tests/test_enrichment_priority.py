"""
Ordering of the synchronous first-pass enrichment batch
(scripts.enrich_all_clusters.prioritize_first_pass).

The synchronous pass is serial, so position in this list is wait time. The
selection query orders by first_seen_at, which is written once at cluster
creation from the earliest article's publish time — so a story that broke
early and was corroborated hours later sorted below trivial clusters created
whole a few minutes ago, and the highest-coverage story in the feed was
enriched last.
"""
from scripts.enrich_all_clusters import prioritize_first_pass


class _StubCluster:
    def __init__(self, name, distinct_source_count):
        self.name = name
        self.distinct_source_count = distinct_source_count

    def __repr__(self):
        return self.name


class TestPrioritizeFirstPass:
    def test_highest_coverage_is_enriched_first(self):
        small, big, mid = _StubCluster("small", 2), _StubCluster("big", 13), _StubCluster("mid", 5)
        batch = [small, big, mid]

        prioritize_first_pass(batch)

        assert batch == [big, mid, small]

    def test_the_lead_story_overtakes_newer_trivial_clusters(self):
        # The real regression: the query hands these over in first_seen_at
        # order, so the 13-outlet lead story arrives last.
        newer_trivial = [_StubCluster(f"trivial{i}", 2) for i in range(30)]
        lead = _StubCluster("lead", 13)
        batch = newer_trivial + [lead]

        prioritize_first_pass(batch)

        assert batch[0] is lead

    def test_sorts_in_place(self):
        # The caller iterates the same list object it passed in.
        batch = [_StubCluster("a", 2), _StubCluster("b", 9)]
        assert prioritize_first_pass(batch) is None
        assert batch[0].name == "b"

    def test_equal_coverage_keeps_the_querys_recency_order(self):
        # Stable sort: clusters that tie on coverage must not be shuffled, so
        # newest-first is still the tiebreak the query intended.
        a, b, c = _StubCluster("a", 4), _StubCluster("b", 4), _StubCluster("c", 4)
        batch = [a, b, c]

        prioritize_first_pass(batch)

        assert batch == [a, b, c]

    def test_a_null_source_count_does_not_crash_the_run(self):
        # distinct_source_count is NOT NULL in the schema, but a backfilled or
        # partially-constructed row must not take down the whole enrichment
        # cycle on a TypeError comparing None to int.
        unknown, big = _StubCluster("unknown", None), _StubCluster("big", 7)
        batch = [unknown, big]

        prioritize_first_pass(batch)

        assert batch == [big, unknown]

    def test_an_empty_batch_is_fine(self):
        batch = []
        prioritize_first_pass(batch)
        assert batch == []
