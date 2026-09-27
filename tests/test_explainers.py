from app.services.explainer_script import MAX_SECTIONS, MIN_SECTIONS, finalize_explainer


def _raw(**overrides):
    raw = {
        "quick_answer": "The rupee is weakening because of a stronger dollar and capital outflows.",
        "sections": [
            {"heading": "What's driving it", "body": "Crude oil prices climbed, so refiners need more dollars."},
            {"heading": "What happens next", "body": "The RBI has intervened twice this month to slow the slide."},
        ],
        "sources": [
            {"title": "RBI's latest forex reserve data", "outlet": "Reserve Bank of India", "url": "https://rbi.org.in"},
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
    assert out["sources"][0]["outlet"] == "Reserve Bank of India"


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


def test_finalize_drops_malformed_sources_but_keeps_valid_ones():
    out = finalize_explainer(_raw(sources=[
        {"title": "Good source", "outlet": "Reuters", "url": "https://reuters.com"},
        {"title": "Missing outlet"},
        "not even a dict",
    ]))
    assert out is not None
    assert len(out["sources"]) == 1
    assert out["sources"][0]["outlet"] == "Reuters"


def test_finalize_rejects_non_dict_input():
    assert finalize_explainer(None) is None
    assert finalize_explainer("not json") is None
    assert finalize_explainer([1, 2, 3]) is None
