from app.services.topic_filters import (
    NORTHEAST_KEYWORDS, keyword_regex, location_keywords_for_query, repeated_keyword_regex,
)
import re


def test_known_place_and_region_alias_resolve():
    assert location_keywords_for_query("Guwahati") == ["guwahati"]
    assert location_keywords_for_query(" North-East ") == NORTHEAST_KEYWORDS
    assert location_keywords_for_query("Modi") is None


def test_single_passing_mention_is_not_central():
    once = "Centre announces scheme; Assam CM welcomed it"
    twice = "Floods hit Assam; Assam govt sets up relief camps"
    rep = re.compile(repeated_keyword_regex(["assam"]).replace(r"\y", r"\b"), re.I | re.S)
    assert not rep.search(once)
    assert rep.search(twice)


def test_whole_word_only():
    p = re.compile(keyword_regex(["arunachal"]).replace(r"\y", r"\b"), re.I)
    assert not p.search("Arunachaleswar temple")
