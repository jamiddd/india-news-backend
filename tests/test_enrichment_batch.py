"""
Pure-logic tests for the Batch API enrichment path: the request shape shared
with the synchronous path, and the result-application rules that a batch's
out-of-order, partially-failed results file makes load-bearing. No DB/network.
"""
import pytest

from app.services.enrichment import (
    apply_ai_response,
    apply_baseline_enrichment,
    build_enrichment_request,
    CONTENT_CAP,
    MULTI_SOURCE_MODEL,
    SINGLE_SOURCE_MODEL,
)


class _StubSource:
    def __init__(self, name):
        self.name = name


class _StubArticle:
    def __init__(self, title, source_id, content=None, snippet=None, name="Outlet"):
        self.title = title
        self.source_id = source_id
        self.content = content
        self.snippet = snippet
        self.source = _StubSource(name)


class _StubCluster:
    def __init__(self, articles, cluster_id=1):
        self.id = cluster_id
        self.articles = articles
        self.headline = "original headline"
        self.summary = None
        self.entities = None
        self.topics = None
        self.framing_comparison = None
        self.ai_enriched = False
        self.last_enriched_at = None


def _response(payload_json: str):
    return {"content": [{"type": "text", "text": payload_json}], "stop_reason": "end_turn"}


class TestBuildEnrichmentRequest:
    """The batch path sends this object as a request's `params`, so anything
    the synchronous path relies on has to be inside it, not around it."""

    def test_multi_source_uses_the_multi_source_model_and_effort(self):
        cluster = _StubCluster([
            _StubArticle("A", 1, content="a" * 100),
            _StubArticle("B", 2, content="b" * 100),
        ])
        body = build_enrichment_request(cluster, can_compare_framing=True)
        assert body["model"] == MULTI_SOURCE_MODEL
        assert body["output_config"] == {"effort": "low"}

    def test_single_source_omits_effort(self):
        # output_config.effort is rejected by the single-source model, so its
        # absence here is a correctness requirement, not a preference.
        cluster = _StubCluster([_StubArticle("A", 1, content="a" * 100)])
        body = build_enrichment_request(cluster, can_compare_framing=False)
        assert body["model"] == SINGLE_SOURCE_MODEL
        assert "output_config" not in body

    def test_system_prompt_stays_cacheable(self):
        cluster = _StubCluster([_StubArticle("A", 1, content="a")])
        body = build_enrichment_request(cluster, can_compare_framing=False)
        assert body["system"][0]["cache_control"] == {"type": "ephemeral"}

    def test_content_is_capped(self):
        cluster = _StubCluster([_StubArticle("A", 1, content="x" * 99999)])
        body = build_enrichment_request(cluster, can_compare_framing=False)
        sent = body["messages"][0]["content"]
        assert "x" * CONTENT_CAP in sent
        assert "x" * (CONTENT_CAP + 1) not in sent

    def test_falls_back_to_snippet_without_content(self):
        cluster = _StubCluster([_StubArticle("A", 1, content=None, snippet="stub text")])
        body = build_enrichment_request(cluster, can_compare_framing=False)
        assert "stub text" in body["messages"][0]["content"]


class TestApplyAiResponse:
    def test_applies_headline_summary_and_marks_enriched(self):
        cluster = _StubCluster([_StubArticle("A", 1)])
        apply_ai_response(cluster, _response(
            '{"neutral_headline": "Neutral", "summary_bullets": ["One.", "Two."]}'
        ), can_compare_framing=False)
        assert cluster.headline == "Neutral"
        assert "One." in cluster.summary and "Two." in cluster.summary
        assert cluster.ai_enriched is True
        # The batch path needs this stamped or the cluster is re-selected as
        # a first-pass candidate forever and never leaves the sync queue.
        assert cluster.last_enriched_at is not None

    def test_never_accepts_framing_for_a_single_source_cluster(self):
        cluster = _StubCluster([_StubArticle("A", 1)])
        apply_ai_response(cluster, _response(
            '{"neutral_headline": "H", "framing_comparison": '
            '[{"outlet": "X", "headline_angle": "invented"}]}'
        ), can_compare_framing=False)
        assert cluster.framing_comparison is None

    def test_honours_an_explicitly_empty_framing_list(self):
        # The fabrication bug: [] is falsy, so a truthiness test silently
        # kept a stale framing instead of accepting the model's correct "none".
        cluster = _StubCluster([_StubArticle("A", 1), _StubArticle("B", 2)])
        cluster.framing_comparison = [{"outlet": "stale", "headline_angle": "old"}]
        apply_ai_response(cluster, _response(
            '{"neutral_headline": "H", "framing_comparison": []}'
        ), can_compare_framing=True)
        assert cluster.framing_comparison is None

    def test_raises_on_a_response_with_no_text_block(self):
        # A batch result can be malformed without the HTTP layer noticing.
        # The caller counts on this raising so it can skip that one cluster
        # and keep the rest of the batch.
        cluster = _StubCluster([_StubArticle("A", 1)])
        with pytest.raises(Exception):
            apply_ai_response(cluster, {"content": [], "stop_reason": "end_turn"},
                              can_compare_framing=False)

    def test_raises_on_unparseable_json(self):
        cluster = _StubCluster([_StubArticle("A", 1)])
        with pytest.raises(Exception):
            apply_ai_response(cluster, _response("not json at all"),
                              can_compare_framing=False)


class TestBaselinePreservesFraming:
    """The write-path half of the blanking fix.

    apply_baseline_enrichment used to null framing_comparison on every call.
    Because poller.py flips ai_enriched to False whenever a cluster gains an
    article, and submit_refinement_batch COMMITS the baseline before posting
    the batch, that null was served for the whole Batch API turnaround — worst
    on the highest-coverage stories, which refine most often.
    """

    def test_keeps_the_previous_framing_for_a_multi_outlet_cluster(self):
        cluster = _StubCluster([
            _StubArticle("A", 1, snippet="x", name="NDTV"),
            _StubArticle("B", 2, snippet="y", name="The Hindu"),
        ])
        cluster.framing_comparison = [{"outlet": "NDTV", "headline_angle": "Official"}]

        apply_baseline_enrichment(cluster)

        # One refinement behind beats a blank panel while the batch is in
        # flight; the successful pass overwrites it.
        assert cluster.framing_comparison == [
            {"outlet": "NDTV", "headline_angle": "Official"}
        ]

    def test_still_clears_framing_a_cluster_can_no_longer_justify(self):
        # The anti-fabrication invariant is unchanged: framing must never
        # outlive the multi-outlet condition that justified it (e.g. after
        # repair_runaway_clusters.py splits a cluster back to one source).
        cluster = _StubCluster([_StubArticle("A", 1, snippet="x", name="NDTV")])
        cluster.framing_comparison = [{"outlet": "NDTV", "headline_angle": "Official"}]

        apply_baseline_enrichment(cluster)

        assert cluster.framing_comparison is None

    def test_a_successful_pass_still_overwrites_the_preserved_framing(self):
        cluster = _StubCluster([
            _StubArticle("A", 1, snippet="x", name="NDTV"),
            _StubArticle("B", 2, snippet="y", name="The Hindu"),
        ])
        cluster.framing_comparison = [{"outlet": "stale", "headline_angle": "old"}]

        can_compare = apply_baseline_enrichment(cluster)
        apply_ai_response(cluster, _response(
            '{"neutral_headline": "H", "framing_comparison": '
            '[{"outlet": "The Hindu", "headline_angle": "fresh"}]}'
        ), can_compare)

        assert cluster.framing_comparison == [
            {"outlet": "The Hindu", "headline_angle": "fresh"}
        ]
