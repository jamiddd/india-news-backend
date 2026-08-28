"""
Pure-logic tests for app/services/image_extractor.py — pulling an image URL
out of a feedparser entry, and is_broken_image_url's HEAD-check filter.
Uses hand-built stub objects standing in for real feedparser.FeedParserDict
entries (attribute access, same shape) and a stub HTTP client. No real
network.
"""
from types import SimpleNamespace

from app.services.image_extractor import extract_rss_image, is_broken_image_url


class _StubEntry:
    """Minimal stand-in for a feedparser entry — only the attributes
    extract_rss_image actually reads, accessed the same way (getattr)."""

    def __init__(self, media_content=None, media_thumbnail=None, links=None, summary=None):
        if media_content is not None:
            self.media_content = media_content
        if media_thumbnail is not None:
            self.media_thumbnail = media_thumbnail
        if links is not None:
            self.links = links
        if summary is not None:
            self.summary = summary


class TestExtractRssImage:
    def test_media_content_preferred(self):
        entry = _StubEntry(media_content=[{"url": "https://example.com/media.jpg"}])
        assert extract_rss_image(entry) == "https://example.com/media.jpg"

    def test_media_thumbnail_used_when_no_media_content(self):
        entry = _StubEntry(media_thumbnail=[{"url": "https://example.com/thumb.jpg"}])
        assert extract_rss_image(entry) == "https://example.com/thumb.jpg"

    def test_media_content_takes_priority_over_thumbnail(self):
        entry = _StubEntry(
            media_content=[{"url": "https://example.com/media.jpg"}],
            media_thumbnail=[{"url": "https://example.com/thumb.jpg"}],
        )
        assert extract_rss_image(entry) == "https://example.com/media.jpg"

    def test_enclosure_link_used_as_fallback(self):
        entry = _StubEntry(
            links=[
                {"rel": "alternate", "type": "text/html", "href": "https://example.com/story"},
                {"rel": "enclosure", "type": "image/jpeg", "href": "https://example.com/enc.jpg"},
            ]
        )
        assert extract_rss_image(entry) == "https://example.com/enc.jpg"

    def test_enclosure_link_ignored_if_not_image_type(self):
        entry = _StubEntry(
            links=[{"rel": "enclosure", "type": "audio/mpeg", "href": "https://example.com/a.mp3"}]
        )
        assert extract_rss_image(entry) is None

    def test_img_tag_in_summary_html_used_as_last_resort(self):
        entry = _StubEntry(summary='<p>Some text <img src="https://example.com/inline.jpg"/></p>')
        assert extract_rss_image(entry) == "https://example.com/inline.jpg"

    def test_no_image_anywhere_returns_none(self):
        entry = _StubEntry(summary="<p>Just plain text, no image.</p>")
        assert extract_rss_image(entry) is None

    def test_completely_empty_entry_returns_none(self):
        entry = _StubEntry()
        assert extract_rss_image(entry) is None

    def test_missing_attributes_dont_raise(self):
        # extract_rss_image uses getattr(..., None) throughout — a bare
        # object with none of the expected attributes should not raise.
        assert extract_rss_image(SimpleNamespace()) is None


class _StubResponse:
    def __init__(self, status_code=200, headers=None):
        self.status_code = status_code
        self.headers = headers or {}


class _StubClient:
    """Stands in for curl_cffi's AsyncSession — only .head() is used."""

    def __init__(self, response=None, exception=None):
        self._response = response
        self._exception = exception

    async def head(self, url, **kwargs):
        if self._exception is not None:
            raise self._exception
        return self._response


class TestIsBrokenImageUrl:
    async def test_none_url_is_not_broken(self):
        assert await is_broken_image_url(_StubClient(), None) is False

    async def test_zero_content_length_is_broken(self):
        client = _StubClient(_StubResponse(200, {"content-length": "0"}))
        assert await is_broken_image_url(client, "https://example.com/img.jpg") is True

    async def test_nonzero_content_length_is_not_broken(self):
        client = _StubClient(_StubResponse(200, {"content-length": "12345"}))
        assert await is_broken_image_url(client, "https://example.com/img.jpg") is False

    async def test_missing_content_length_fails_open(self):
        client = _StubClient(_StubResponse(200, {}))
        assert await is_broken_image_url(client, "https://example.com/img.jpg") is False

    async def test_error_status_fails_open(self):
        client = _StubClient(_StubResponse(404, {"content-length": "0"}))
        assert await is_broken_image_url(client, "https://example.com/img.jpg") is False

    async def test_request_exception_fails_open(self):
        client = _StubClient(exception=TimeoutError("boom"))
        assert await is_broken_image_url(client, "https://example.com/img.jpg") is False
