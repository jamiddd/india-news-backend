"""
Pure-logic tests for app/services/entity_graph.py's canonicalize_entity —
the string normalization + alias lookup that keeps entity_stats mentions
from fragmenting across spelling variants. No DB/network.
"""
from app.services.entity_graph import canonicalize_entity


class TestCanonicalizeEntity:
    def test_known_alias_variants_collapse_to_same_key(self):
        assert canonicalize_entity("RBI", "organization") == canonicalize_entity(
            "Reserve Bank of India", "organization"
        )

    def test_case_and_whitespace_insensitive(self):
        assert canonicalize_entity("  rbi  ", "organization") == canonicalize_entity(
            "RBI", "organization"
        )

    def test_honorific_stripped(self):
        assert canonicalize_entity("Dr. S. Jaishankar", "person") == canonicalize_entity(
            "S. Jaishankar", "person"
        )

    def test_unknown_entity_canonicalizes_to_normalized_self(self):
        key = canonicalize_entity("Some New Institute", "organization")
        assert key == "organization:some_new_institute"

    def test_type_scoping_prevents_cross_type_collision(self):
        # Same normalized string, different entity_type, must not collide.
        person_key = canonicalize_entity("Delhi", "person")
        location_key = canonicalize_entity("Delhi", "location")
        assert person_key != location_key

    def test_empty_or_whitespace_returns_none(self):
        assert canonicalize_entity("", "person") is None
        assert canonicalize_entity("   ", "organization") is None

    def test_new_delhi_and_ncr_alias_to_delhi(self):
        assert canonicalize_entity("New Delhi", "location") == canonicalize_entity(
            "NCR", "location"
        )
