"""
Pure-logic tests for app/services/dedup.py — URL canonicalization/hashing
(exact dedup) and SimHash near-duplicate clustering. No DB/network needed.
"""
from app.services.dedup import (
    canonicalize_url,
    compute_url_hash,
    normalize_text,
    to_signed_64,
    to_unsigned_64,
    compute_simhash,
    hamming_distance,
    is_near_duplicate,
    shares_topic,
    title_tokens,
)


class TestCanonicalizeUrl:
    def test_strips_tracking_params(self):
        url = "https://example.com/story?utm_source=twitter&utm_medium=social&id=42"
        result = canonicalize_url(url)
        assert "utm_source" not in result
        assert "utm_medium" not in result
        assert "id=42" in result

    def test_strips_all_known_tracking_params(self):
        url = "https://example.com/a?ref=x&gclid=y&fbclid=z&cmpid=w&at_custom1=v"
        result = canonicalize_url(url)
        for param in ("ref", "gclid", "fbclid", "cmpid", "at_custom1"):
            assert f"{param}=" not in result

    def test_lowercases_scheme_and_netloc(self):
        assert canonicalize_url("HTTPS://Example.COM/Path") == canonicalize_url(
            "https://example.com/Path"
        )

    def test_strips_trailing_slash(self):
        assert canonicalize_url("https://example.com/story/") == canonicalize_url(
            "https://example.com/story"
        )

    def test_no_fragment_in_output(self):
        result = canonicalize_url("https://example.com/story#section2")
        assert "#" not in result
        assert "section2" not in result

    def test_preserves_non_tracking_query_params(self):
        result = canonicalize_url("https://example.com/story?id=42&page=2")
        assert "id=42" in result
        assert "page=2" in result


class TestComputeUrlHash:
    def test_deterministic(self):
        url = "https://example.com/story?id=42"
        assert compute_url_hash(url) == compute_url_hash(url)

    def test_tracking_params_dont_change_hash(self):
        base = compute_url_hash("https://example.com/story?id=42")
        with_tracking = compute_url_hash(
            "https://example.com/story?id=42&utm_source=twitter&fbclid=abc"
        )
        assert base == with_tracking

    def test_different_urls_different_hash(self):
        assert compute_url_hash("https://example.com/a") != compute_url_hash(
            "https://example.com/b"
        )

    def test_returns_sha256_hex_digest(self):
        result = compute_url_hash("https://example.com/story")
        assert len(result) == 64
        int(result, 16)  # raises ValueError if not valid hex


class TestNormalizeText:
    def test_empty_string(self):
        assert normalize_text("") == ""

    def test_none_like_falsy(self):
        assert normalize_text(None) == ""

    def test_strips_html_tags(self):
        assert normalize_text("<b>Hello</b> world") == "hello world"

    def test_strips_punctuation(self):
        assert normalize_text("Hello, world! It's a test.") == "hello world it s a test"

    def test_lowercases_and_collapses_whitespace(self):
        assert normalize_text("  HELLO   World  ") == "hello world"


class TestSigned64Roundtrip:
    def test_roundtrip_positive(self):
        val = 12345
        assert to_unsigned_64(to_signed_64(val)) == val

    def test_roundtrip_high_bit_set(self):
        # A value with bit 63 set becomes negative when interpreted as signed.
        val = (1 << 63) + 100
        signed = to_signed_64(val)
        assert signed < 0
        assert to_unsigned_64(signed) == val

    def test_small_values_pass_through(self):
        assert to_signed_64(0) == 0
        assert to_unsigned_64(0) == 0


class TestComputeSimhash:
    def test_empty_text_returns_zero(self):
        assert compute_simhash("", "") == 0

    def test_whitespace_only_returns_zero(self):
        assert compute_simhash("   ", None) == 0

    def test_identical_titles_produce_identical_hash(self):
        h1 = compute_simhash("Modi announces new policy")
        h2 = compute_simhash("Modi announces new policy")
        assert h1 == h2

    def test_returns_int(self):
        assert isinstance(compute_simhash("Some headline here"), int)

    def test_snippet_influences_hash(self):
        h1 = compute_simhash("Modi announces new policy")
        h2 = compute_simhash("Modi announces new policy", "Additional context here")
        # Not guaranteed to differ in general, but with a real added snippet
        # of unrelated tokens it should for this fingerprint algorithm.
        assert h1 != h2


class TestHammingDistance:
    def test_identical_hashes_zero_distance(self):
        h = compute_simhash("Modi announces new policy today")
        assert hamming_distance(h, h) == 0

    def test_distance_is_symmetric(self):
        h1 = compute_simhash("Modi announces new policy")
        h2 = compute_simhash("Completely unrelated sports cricket match result")
        assert hamming_distance(h1, h2) == hamming_distance(h2, h1)

    def test_handles_negative_signed_hashes(self):
        # to_signed_64 can produce negative ints; hamming_distance must
        # still work correctly via the unsigned round-trip.
        h1 = to_signed_64((1 << 63) + 5)
        h2 = to_signed_64((1 << 63) + 7)
        assert hamming_distance(h1, h2) == bin(5 ^ 7).count("1")


class TestIsNearDuplicate:
    def test_near_duplicate_titles(self):
        h1 = compute_simhash("RBI cuts repo rate by 25 basis points")
        h2 = compute_simhash("RBI cuts repo rate by 25 bps")
        assert is_near_duplicate(h1, h2, max_distance=10)

    def test_unrelated_titles_not_duplicate(self):
        h1 = compute_simhash(
            "RBI cuts repo rate by 25 basis points in monetary policy review"
        )
        h2 = compute_simhash(
            "India wins cricket World Cup final against Australia in thriller"
        )
        assert not is_near_duplicate(h1, h2, max_distance=4)

    def test_empty_hash_sentinel_never_matches(self):
        h = compute_simhash("Some real headline with real words")
        assert not is_near_duplicate(0, h)
        assert not is_near_duplicate(h, 0)
        assert not is_near_duplicate(0, 0)

    def test_exact_threshold_boundary(self):
        # Two hashes at exactly max_distance apart should count as near-dup
        # (<=), one past it should not. Neither operand can be 0 here — that
        # hits the empty-hash sentinel branch regardless of distance (see
        # test_empty_hash_sentinel_never_matches), so these are constructed
        # relative to a nonzero base instead.
        h1 = 1
        h2_at_distance_4 = h1 ^ 0b11110  # differs in exactly 4 bits
        h2_at_distance_5 = h1 ^ 0b111110  # differs in exactly 5 bits
        assert hamming_distance(h1, h2_at_distance_4) == 4
        assert hamming_distance(h1, h2_at_distance_5) == 5
        assert is_near_duplicate(h1, h2_at_distance_4, max_distance=4)
        assert not is_near_duplicate(h1, h2_at_distance_5, max_distance=4)


class TestTitleTokens:
    def test_drops_stopwords_and_short_words(self):
        assert title_tokens("The RBI cuts repo rate by 25 bps") == {"cuts", "repo", "rate"}

    def test_ignores_snippet_entirely(self):
        # title_tokens takes only a title — the snippet exclusion is the whole
        # point of the function (see its docstring).
        assert title_tokens("Modi meets Putin") == {"modi", "meets", "putin"}

    def test_empty_title(self):
        assert title_tokens("") == set()

    def test_devanagari_survives_normalisation(self):
        # normalize_text keeps Unicode marks; a Hindi headline must not be
        # shattered into nothing (regression: identical PIB titles once
        # compared as sharing no topic).
        assert len(title_tokens("प्रधानमंत्री ने बैठक की अध्यक्षता की")) > 0


class TestSharesTopic:
    """The confirmation gate. Thresholds come from the grid search in
    scripts/eval_clustering.py — see MIN_TITLE_JACCARD in dedup.py."""

    def test_same_story_different_outlets(self):
        assert shares_topic(
            "India rejects Court of Arbitration's Indus Waters Treaty award",
            "India rejects Indus Waters arbitration award",
        )

    def test_unrelated_headlines(self):
        assert not shares_topic(
            "India rejects Indus Waters arbitration award",
            "Bengaluru techie arrested in online fraud case",
        )

    def test_shared_common_words_alone_insufficient(self):
        # Both mention "minister" and "government" but describe different
        # events — the Jaccard floor is what rejects this.
        assert not shares_topic(
            "Minister announces new government housing policy for farmers",
            "Minister resigns as government faces opposition over fuel prices",
        )

    def test_templated_headlines_defeat_the_lexical_gate(self):
        """Documents a real limitation, not desired behaviour.

        These two Yahoo Finance headlines are about completely different
        companies, but share "2026 / earnings / call / transcript" — Jaccard
        0.5, comfortably over the threshold. shares_topic CANNOT tell them
        apart, and on the evaluation fixture this fused 31 unrelated
        transcripts into one "story" (and 29 Bloomberg executive profiles, and
        24 job listings).

        What actually prevents it in production is the same-source guard in
        poller.ingest_source: every one of those headlines comes from a single
        outlet, and two articles from one outlet are never merged. That means
        the protection is structural, not lexical — if two DIFFERENT outlets
        both published templated headlines sharing this much boilerplate, they
        would still merge incorrectly. If that ever shows up in the feed, the
        fix is IDF-weighted overlap (already implemented and measured in
        scripts/eval_clustering.py as the `idf_overlap` metric), not a higher
        Jaccard threshold.
        """
        assert shares_topic(
            "Affirm (AFRM) Q4 2026 Earnings Call Transcript",
            "Ulta Beauty (ULTA) Q2 2026 Earnings Call Transcript",
        )

    def test_empty_titles_never_match(self):
        assert not shares_topic("", "India rejects arbitration award")
        assert not shares_topic("India rejects arbitration award", "")

    def test_identical_titles_match(self):
        title = "Nepal flash floods death toll climbs to 903"
        assert shares_topic(title, title)

    def test_min_shared_is_enforced(self):
        # One shared significant token can never be enough, regardless of how
        # short both headlines are.
        assert not shares_topic("Indus treaty", "Indus ruling", min_shared=2)
