"""
Pure-logic tests for app/services/enrichment.py's non-network pieces:
parse_json_response (the fence-stripping fallback chain that's already
broken once in production — see india-news-app-handoff.md §11 gotcha #6),
extract_entities_rule_based, and generate_framing_comparison. No DB/network.
"""
import json

import pytest

from app.services.enrichment import (
    parse_json_response,
    extract_entities_rule_based,
    generate_framing_comparison,
    distinct_source_count,
)


class _StubSource:
    def __init__(self, name):
        self.name = name


class _StubArticle:
    def __init__(self, title, source_name, source_id=None):
        self.title = title
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
        assert result == {"persons": [], "organizations": [], "locations": []}

    def test_no_duplicate_entities(self):
        text = "RBI RBI RBI all mentioned the RBI repeatedly."
        result = extract_entities_rule_based(text)
        assert result["organizations"].count("RBI") == 1


class TestGenerateFramingComparison:
    def test_official_policy_angle(self):
        # Trigger words are matched literally ("announced", not "announces")
        # — this test title is deliberately exact, not just close.
        articles = [_StubArticle("Government announced new scheme", "NDTV")]
        result = generate_framing_comparison(articles)
        assert result[0]["headline_angle"] == "Official / Policy Statement"

    def test_conflict_angle(self):
        articles = [_StubArticle("Protest breaks out over new law", "Hindustan Times")]
        result = generate_framing_comparison(articles)
        assert result[0]["headline_angle"] == "Conflict & Opposition Impact"

    def test_financial_angle(self):
        articles = [_StubArticle("Sensex hits record high in early trade", "Moneycontrol")]
        result = generate_framing_comparison(articles)
        assert result[0]["headline_angle"] == "Financial & Market Impact"

    def test_general_reporting_fallback(self):
        articles = [_StubArticle("A quiet day in the neighborhood", "Local News")]
        result = generate_framing_comparison(articles)
        assert result[0]["headline_angle"] == "General Reporting"

    def test_missing_source_falls_back_to_generic_label(self):
        articles = [_StubArticle("Some headline", None)]
        result = generate_framing_comparison(articles)
        assert result[0]["outlet"] == "Source"

    def test_preserves_article_order(self):
        articles = [
            _StubArticle("First headline", "Outlet A"),
            _StubArticle("Second headline", "Outlet B"),
        ]
        result = generate_framing_comparison(articles)
        assert [r["outlet"] for r in result] == ["Outlet A", "Outlet B"]

    def test_empty_list_returns_empty(self):
        assert generate_framing_comparison([]) == []


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
