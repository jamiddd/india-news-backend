from app.services.explainer_script import (
    MAX_SECTIONS,
    MIN_SECTIONS,
    SourceExcerpt,
    _source_block,
    finalize_explainer,
)
from app.services.explainer_sourcing import candidate_phrases


def _raw(**overrides):
    raw = {
        "quick_answer": "The rupee is weakening because of a stronger dollar and capital outflows.",
        "sections": [
            {"heading": "What's driving it", "body": "Crude oil prices climbed, so refiners need more dollars."},
            {"heading": "What happens next", "body": "The RBI has intervened twice this month to slow the slide."},
        ],
    }
    raw.update(overrides)
    return raw


def test_finalize_accepts_a_well_shaped_explainer():
    out = finalize_explainer(_raw())
    assert out is not None
    assert out["quick_answer"].startswith("The rupee is weakening")
    assert len(out["sections"]) == 2
    assert out["sections"][0]["heading"] == "What's driving it"
    assert "sources" not in out  # Claude is never asked for sources; explainer.py derives them from the DB


def test_finalize_rejects_missing_quick_answer():
    assert finalize_explainer(_raw(quick_answer="")) is None
    assert finalize_explainer(_raw(quick_answer=None)) is None


def test_finalize_rejects_too_few_or_too_many_sections():
    assert finalize_explainer(_raw(sections=[{"heading": "Only one", "body": "Not enough sections."}])) is None
    too_many = [{"heading": f"Section {i}", "body": "Body text."} for i in range(MAX_SECTIONS + 1)]
    assert finalize_explainer(_raw(sections=too_many)) is None
    assert MIN_SECTIONS <= 2  # sanity: the "only one section" case above must actually be below the floor


def test_finalize_rejects_a_section_missing_heading_or_body():
    bad = [
        {"heading": "", "body": "Has a body but no heading."},
        {"heading": "Has a heading", "body": ""},
    ]
    assert finalize_explainer(_raw(sections=bad)) is None


def test_finalize_ignores_extra_keys_like_a_stray_sources_list():
    # Claude occasionally adds an unrequested field despite the prompt; the
    # validator should just ignore it rather than choke on it.
    out = finalize_explainer(_raw(sources=[{"title": "invented", "outlet": "invented"}]))
    assert out is not None
    assert "sources" not in out


def test_finalize_rejects_non_dict_input():
    assert finalize_explainer(None) is None
    assert finalize_explainer("not json") is None
    assert finalize_explainer([1, 2, 3]) is None


def _excerpt(**overrides):
    defaults = dict(
        cluster_id=501,
        headline="RBI intervenes to steady the rupee",
        summary="The central bank sold dollars from reserves twice this month.",
        distinct_source_count=6,
        outlets=["Reuters", "Mint", "PIB"],
        article_titles=["RBI sells dollars to steady rupee", "Rupee hits fresh low amid FPI outflows"],
    )
    defaults.update(overrides)
    return SourceExcerpt(**defaults)


def test_source_block_includes_real_excerpt_fields_the_prompt_can_ground_on():
    block = _source_block([_excerpt()])
    assert "[Story 501]" in block
    assert "RBI intervenes to steady the rupee" in block
    assert "Reuters" in block
    assert "RBI sells dollars to steady rupee" in block


def test_candidate_phrases_tries_distinctive_single_words_before_multi_word_phrases():
    # "CBSE" (an acronym) should be tried before any multi-word phrase, even
    # though it's not the first word in the sentence — distinctive single
    # words go first regardless of position (see the function's docstring
    # for why: the word that actually finds background clusters is often
    # buried deep in a summary sentence).
    phrases = candidate_phrases("Supreme Court directs CBSE to extend third-language policy exemptions")
    multi_word = [p for p in phrases if " " in p]
    assert "CBSE" in phrases
    assert not multi_word or phrases.index("CBSE") < phrases.index(multi_word[0])


def test_candidate_phrases_finds_a_distinctive_word_buried_at_the_end_of_a_long_summary():
    # Regression case: an earlier version of this function exhausted its
    # phrase budget on 4-word windows from the headline alone, so a
    # differently-spelled distinctive term sitting near the end of a long
    # summary sentence never got tried. "three-language" here plays that
    # role, mirroring the real CBSE test run where the headline said
    # "third-language" but the summary said "three-language".
    headline = "Supreme Court directs CBSE to extend third-language policy exemptions to Class VI students"
    summary = (
        "The Supreme Court directed CBSE to grant Class VI students the same exemptions and "
        "relaxations available to Classes VII and VIII under the newly introduced three-language scheme."
    )
    phrases = candidate_phrases(headline, summary)
    assert "three-language" in phrases


def test_candidate_phrases_drops_stopwords_and_short_filler():
    phrases = candidate_phrases("Court directs CBSE to extend policy")
    assert not any(p.split() == ["to"] for p in phrases)
    assert not any(p.startswith("to ") or p.endswith(" to") for p in phrases)


def test_candidate_phrases_preserves_original_casing_and_hyphenation():
    # Case/hyphenation must survive verbatim since these strings feed a
    # literal ILIKE search against other clusters' headline/summary text.
    phrases = candidate_phrases("Supreme Court directs CBSE to extend third-language policy")
    assert any("third-language" in p for p in phrases)
    assert any("CBSE" in p for p in phrases)


def test_candidate_phrases_pulls_from_both_headline_and_summary():
    phrases = candidate_phrases(
        "Supreme Court directs CBSE to extend third-language policy exemptions",
        "Extends relief under the newly introduced three-language scheme",
    )
    assert any("three-language" in p for p in phrases)


def test_candidate_phrases_deduplicates_and_respects_max_phrases():
    phrases = candidate_phrases("test test test test test", max_phrases=3)
    assert len(phrases) <= 3
    assert len(phrases) == len(set(phrases))
