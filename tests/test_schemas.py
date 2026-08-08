"""
Validation/round-trip tests for app/schemas.py's Pydantic models. No DB
needed — from_attributes=True schemas are tested against lightweight stub
objects rather than real ORM instances.
"""
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.schemas import (
    SourceOut,
    ArticleOut,
    StoryClusterOut,
    PaginatedClustersOut,
    UserPreferences,
    UserAuthRequest,
    UserAuthResponse,
)


class TestUserAuthRequest:
    def test_valid_request(self):
        req = UserAuthRequest(
            email="user@example.com", display_name="Test User", provider="email", uid="token123"
        )
        assert req.email == "user@example.com"
        assert req.uid == "token123"

    def test_uid_defaults_to_none(self):
        req = UserAuthRequest(email="user@example.com", display_name="Test", provider="google")
        assert req.uid is None

    def test_missing_required_field_raises(self):
        with pytest.raises(ValidationError):
            UserAuthRequest(display_name="Test", provider="email")  # no email

    def test_email_is_plain_str_not_validated_as_email_format(self):
        # Pinning a real, current gap (not fixing it here): `email: str`,
        # not pydantic.EmailStr, so a malformed "email" currently passes
        # schema validation with no error. Actual verification only happens
        # via Firebase ID token verification downstream, not this schema.
        req = UserAuthRequest(email="not-an-email-at-all", display_name="Test", provider="email")
        assert req.email == "not-an-email-at-all"


class TestUserPreferences:
    def test_all_defaults(self):
        prefs = UserPreferences()
        assert prefs.theme_mode == "system"
        assert prefs.accent_color == "blue"
        assert prefs.language_pref == "all"
        assert prefs.enabled_categories == []
        assert prefs.custom_categories == []

    def test_custom_values(self):
        prefs = UserPreferences(
            theme_mode="dark",
            accent_color="green",
            enabled_categories=["national", "business"],
        )
        assert prefs.theme_mode == "dark"
        assert prefs.enabled_categories == ["national", "business"]

    def test_roundtrip_via_model_dump_and_validate(self):
        original = UserPreferences(theme_mode="dark", enabled_categories=["all", "sports"])
        dumped = original.model_dump()
        restored = UserPreferences.model_validate(dumped)
        assert restored == original

    def test_default_factory_lists_are_independent_between_instances(self):
        # Field(default_factory=list) should give each instance its own
        # list, not a shared mutable default.
        p1 = UserPreferences()
        p2 = UserPreferences()
        p1.enabled_categories.append("national")
        assert p2.enabled_categories == []


class TestUserAuthResponse:
    def test_valid_response(self):
        resp = UserAuthResponse(
            user_id="usr_abc123",
            email="user@example.com",
            display_name="Test User",
            preferences=UserPreferences(),
        )
        assert resp.token is None  # optional, unused field per the handoff doc

    def test_requires_preferences(self):
        with pytest.raises(ValidationError):
            UserAuthResponse(user_id="usr_1", email="a@b.com", display_name="Test")


class TestSourceOut:
    def test_from_stub_attributes_object(self):
        class StubSource:
            id = 1
            name = "The Hindu"
            slug = "the-hindu"
            feed_url = "https://example.com/feed"
            homepage_url = None
            language = "en"
            category = "national"
            region = "national"
            status = "active"

        out = SourceOut.model_validate(StubSource())
        assert out.name == "The Hindu"
        assert out.homepage_url is None


def _make_stub_article():
    class StubArticle:
        id = 1
        source_id = 1
        source_name = "The Hindu"
        url = "https://example.com/story"
        title = "A headline"
        snippet = "A snippet"
        content = None
        author = None
        published_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        image_url = None

    return StubArticle()


class TestArticleOut:
    def test_from_stub_attributes_object(self):
        out = ArticleOut.model_validate(_make_stub_article())
        assert out.title == "A headline"
        assert out.content is None


class TestStoryClusterOutAndPagination:
    def _stub_cluster(self, article_out):
        class StubCluster:
            id = 1
            headline = "Neutral headline"
            summary = "A summary"
            article_count = 1
            first_seen_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
            last_updated_at = datetime(2026, 1, 2, tzinfo=timezone.utc)
            entities = {"persons": [], "organizations": ["RBI"], "locations": []}
            topics = ["Economy & Markets"]
            framing_comparison = [{"outlet": "NDTV", "headline_angle": "Official"}]
            articles = [article_out]

        return StubCluster()

    def test_cluster_with_nested_articles(self):
        article = ArticleOut.model_validate(_make_stub_article())
        cluster_out = StoryClusterOut.model_validate(self._stub_cluster(article))
        assert cluster_out.article_count == 1
        assert cluster_out.articles[0].title == "A headline"
        assert cluster_out.entities["organizations"] == ["RBI"]

    def test_cluster_defaults_empty_articles_list(self):
        cluster_out = StoryClusterOut(
            id=1,
            headline="H",
            article_count=0,
            first_seen_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            last_updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        assert cluster_out.articles == []
        assert cluster_out.summary is None

    def test_paginated_clusters_next_cursor_optional(self):
        cluster_out = StoryClusterOut(
            id=1,
            headline="H",
            article_count=0,
            first_seen_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            last_updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        page = PaginatedClustersOut(items=[cluster_out], has_more=False)
        assert page.next_cursor is None
        assert page.has_more is False

    def test_paginated_clusters_requires_has_more(self):
        with pytest.raises(ValidationError):
            PaginatedClustersOut(items=[])  # has_more has no default
