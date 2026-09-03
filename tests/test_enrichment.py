"""
Pure-logic tests for app/services/enrichment.py's non-network pieces:
parse_json_response (the fence-stripping fallback chain that's already
broken once in production — see india-news-app-handoff.md §11 gotcha #6),
extract_entities_rule_based, and distinct_source_count. No DB/network.
"""
import json

import pytest

from app.services.enrichment import (
    parse_json_response,
    extract_entities_rule_based,
    distinct_source_count,
    extract_text,
    select_articles_for_prompt,
    MAX_ARTICLES,
)
from app.services.poller import should_reenrich_on_new_outlet


class _StubSource:
    def __init__(self, name):
        self.name = name


class _StubArticle:
    def __init__(self, title, source_name, source_id=None, content=None, snippet=None):
        self.title = title
        self.content = content
        self.snippet = snippet
        self.source = _StubSource(source_name) if source_name else None
        # Defaults to hashing the name so existing tests that don't care
        # about source identity still get one distinct id per outlet.
        self.source_id = source_id if source_id is not None else (
            hash(source_name) if source_name else None
        )


class TestParseJsonResponse:
    def test_raw_json(self):
        result = parse_json_response('{"neutral_headline": "Test"}')
        assert result == {"neutral_headline": "Test"}

    def test_fenced_json_with_language_tag(self):
        text = '```json\n{"neutral_headline": "Test"}\n```'
        assert parse_json_response(text) == {"neutral_headline": "Test"}

    def test_fenced_json_without_language_tag(self):
        text = '```\n{"neutral_headline": "Test"}\n```'
        assert parse_json_response(text) == {"neutral_headline": "Test"}

    def test_fenced_json_with_surrounding_commentary(self):
        # Real observed claude-haiku-4-5 behavior per the enrichment.py
        # docstring/handoff doc: sometimes adds commentary around the fence
        # on richer prompts. The regex anchors ^```...```$ so this only
        # parses correctly if the fence itself is what's matched raw-first
        # (raw parse fails on the whole blob) then via the fence extraction
        # — commentary strictly outside the fence, fence at start of string.
        text = '```json\n{"neutral_headline": "Test"}\n```\n\nHope that helps!'
        # The raw parse fails (trailing prose), the fence regex requires the
        # match to start at the beginning of the string (^) but doesn't
        # require it to consume the whole string, so this should still
        # extract via the fence branch.
        result = parse_json_response(text)
        assert result == {"neutral_headline": "Test"}

    def test_brace_extraction_fallback_with_leading_prose(self):
        text = 'Here is the analysis:\n{"neutral_headline": "Test"}'
        assert parse_json_response(text) == {"neutral_headline": "Test"}

    def test_no_json_anywhere_raises_value_error(self):
        with pytest.raises(ValueError):
            parse_json_response("This response has no JSON in it at all.")

    def test_malformed_json_inside_braces_raises_json_decode_error_not_value_error(self):
        # Pinning existing behavior, not "fixing" it: the brace-extraction
        # branch (parse_json_response's last resort) does NOT catch
        # JSONDecodeError the way the raw-parse and fence branches do, so
        # malformed content inside a {...} span propagates a raw
        # json.JSONDecodeError instead of the function's own ValueError.
        # Worth knowing if a caller ever assumes only ValueError can escape.
        text = "{not: valid, json at all}"
        with pytest.raises(json.JSONDecodeError):
            parse_json_response(text)

    def test_realistic_full_response_shape(self):
        text = (
            '```json\n'
            '{\n'
            '  "neutral_headline": "RBI cuts repo rate by 25 bps",\n'
            '  "summary_bullets": ["Rate cut announced", "Third cut this year"],\n'
            '  "entities": {"persons": [], "organizations": ["RBI"], "locations": []},\n'
            '  "topics": ["Economy & Markets"],\n'
            '  "framing_comparison": [{"outlet": "NDTV", "headline_angle": "Official"}]\n'
            '}\n'
            '```'
        )
        result = parse_json_response(text)
        assert result["neutral_headline"] == "RBI cuts repo rate by 25 bps"
        assert result["entities"]["organizations"] == ["RBI"]
        assert len(result["summary_bullets"]) == 2


class TestExtractEntitiesRuleBased:
    def test_finds_known_organization(self):
        result = extract_entities_rule_based("The RBI announced a new policy today.")
        assert "RBI" in result["organizations"]

    def test_finds_known_location(self):
        result = extract_entities_rule_based("The event took place in Mumbai.")
        assert "Mumbai" in result["locations"]

    def test_case_insensitive_matching(self):
        result = extract_entities_rule_based("the rbi cut rates")
        assert "RBI" in result["organizations"]

    def test_word_boundary_avoids_false_positives(self):
        # "SBI" shouldn't match as a substring inside a longer word/ticker.
        result = extract_entities_rule_based("SBI announced quarterly results today.")
        assert "SBI" in result["organizations"]  # real standalone mention
        result2 = extract_entities_rule_based("SBIN stock surged in trading today.")
        assert "SBI" not in result2["organizations"]

    def test_no_matches_returns_empty_lists(self):
        result = extract_entities_rule_based("A completely unrelated sentence about cats.")
        assert result == {"persons": [], "organizations": [], "locations": [], "backdrop": []}

    def test_no_duplicate_entities(self):
        text = "RBI RBI RBI all mentioned the RBI repeatedly."
        result = extract_entities_rule_based(text)
        assert result["organizations"].count("RBI") == 1


class TestDistinctSourceCount:
    """Framing eligibility is defined on OUTLETS, not articles — one outlet
    republishing itself is not a cross-outlet contrast."""

    def test_counts_distinct_outlets_not_articles(self):
        articles = [
            _StubArticle("A", "Times of India", source_id=1),
            _StubArticle("B", "Times of India", source_id=1),
            _StubArticle("C", "The Hindu", source_id=2),
        ]
        assert distinct_source_count(articles) == 2

    def test_single_outlet_many_articles_is_one(self):
        articles = [_StubArticle(f"A{i}", "Yahoo Finance", source_id=7) for i in range(5)]
        assert distinct_source_count(articles) == 1

    def test_empty(self):
        assert distinct_source_count([]) == 0

    def test_ignores_null_source_ids(self):
        articles = [
            _StubArticle("A", None, source_id=None),
            _StubArticle("B", "The Hindu", source_id=2),
        ]
        assert distinct_source_count(articles) == 1

    def test_gate_boundary(self):
        # The exact condition enrich_cluster_with_ai uses.
        one = [_StubArticle("A", "NDTV", source_id=1)]
        two = one + [_StubArticle("B", "Mint", source_id=2)]
        assert not (distinct_source_count(one) >= 2)
        assert distinct_source_count(two) >= 2


class TestShouldReenrichOnNewOutlet:
    """Re-enrichment gate. Resetting ai_enriched on every joining outlet cost
    O(outlets) paid passes per story and kept the enrich timer saturated
    (~$20 over 2026-09-02/03); this bounds it to the doubling thresholds."""

    def test_fires_on_the_transition_that_unlocks_framing(self):
        # 1 -> 2 is the one that must never be skipped: below 2 outlets there
        # is no framing comparison to generate at all.
        assert should_reenrich_on_new_outlet(2) is True

    def test_does_not_fire_for_a_singleton(self):
        assert should_reenrich_on_new_outlet(1) is False

    def test_fires_only_at_doubling_thresholds(self):
        fired = [n for n in range(1, 33) if should_reenrich_on_new_outlet(n)]
        assert fired == [2, 4, 8, 16, 32]

    def test_a_sixteen_outlet_story_costs_four_passes_not_fifteen(self):
        assert sum(should_reenrich_on_new_outlet(n) for n in range(2, 17)) == 4


class TestExtractText:
    """Regression tests for the KeyError: 'text' that broke every
    multi-source enrichment from 2026-09-02 22:54 onward. claude-sonnet-5
    runs adaptive thinking by default, so content[0] is a thinking block."""

    def test_skips_leading_thinking_block(self):
        # The exact shape that raised KeyError: 'text' in production.
        data = {"content": [
            {"type": "thinking", "thinking": "Let me compare these outlets..."},
            {"type": "text", "text": '{"neutral_headline": "x"}'},
        ]}
        assert extract_text(data) == '{"neutral_headline": "x"}'

    def test_plain_text_only_response_still_works(self):
        data = {"content": [{"type": "text", "text": "hello"}]}
        assert extract_text(data) == "hello"

    def test_concatenates_multiple_text_blocks(self):
        data = {"content": [
            {"type": "text", "text": '{"a":'},
            {"type": "text", "text": ' 1}'},
        ]}
        assert extract_text(data) == '{"a": 1}'

    def test_no_text_block_returns_empty_not_keyerror(self):
        # Caller turns this into an explicit, diagnosable ValueError rather
        # than feeding "" to the JSON parser.
        data = {"content": [{"type": "thinking", "thinking": "..."}]}
        assert extract_text(data) == ""

    def test_empty_content_returns_empty(self):
        assert extract_text({"content": []}) == ""
        assert extract_text({}) == ""


class TestSelectArticlesForPrompt:
    """The MAX_ARTICLES cap has to stay outlet-diverse. Taking articles in
    list order would let one prolific outlet fill the cap on its own, which
    is both a worse summary and a framing comparison with nothing to
    compare. See docs/multi-source-feed-plan.md §5.F."""

    def test_keeps_everything_under_the_cap(self):
        articles = [_StubArticle(f"A{i}", f"Outlet{i}", source_id=i) for i in range(3)]
        assert len(select_articles_for_prompt(articles)) == 3

    def test_caps_at_max_articles(self):
        articles = [_StubArticle(f"A{i}", f"Outlet{i}", source_id=i) for i in range(20)]
        assert len(select_articles_for_prompt(articles)) == MAX_ARTICLES

    def test_one_prolific_outlet_cannot_fill_the_cap(self):
        # 10 articles from one outlet, 2 from others. The naive "first six"
        # would send six articles from Outlet1 and no comparison at all.
        articles = [_StubArticle(f"A{i}", "Outlet1", source_id=1) for i in range(10)]
        articles.append(_StubArticle("B", "Outlet2", source_id=2))
        articles.append(_StubArticle("C", "Outlet3", source_id=3))

        selected = select_articles_for_prompt(articles)
        assert len(selected) == MAX_ARTICLES
        assert {a.source_id for a in selected} == {1, 2, 3}
        # One per outlet before any outlet repeats.
        assert [a.source_id for a in selected[:3]] == [1, 2, 3]

    def test_a_multi_source_cluster_never_narrows_to_one_outlet(self):
        # The cap must not contradict can_compare_framing, which is computed
        # on the cluster's full article list.
        articles = [_StubArticle(f"A{i}", "Outlet1", source_id=1) for i in range(50)]
        articles.append(_StubArticle("Z", "Outlet2", source_id=2))
        selected = select_articles_for_prompt(articles)
        assert distinct_source_count(selected) >= 2

    def test_prefers_the_longer_body_within_an_outlet(self):
        # An RSS stub is less use than a real scraped body from the same
        # outlet, and only one of the two survives the round-robin.
        stub = _StubArticle("stub", "Outlet1", source_id=1, snippet="short")
        full = _StubArticle("full", "Outlet1", source_id=1, content="x" * 2000)
        selected = select_articles_for_prompt([stub, full], max_articles=1)
        assert selected == [full]

    def test_empty(self):
        assert select_articles_for_prompt([]) == []
