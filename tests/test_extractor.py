"""Pure-logic tests for app/services/extractor.py's URL classifiers."""
from app.services.extractor import _extract_video_object_media, is_expiring_signed_video_url


class TestIsExpiringSignedVideoUrl:
    def test_boltdns_manifest_with_fastly_token_is_expiring(self):
        url = (
            "https://manifest.prod.boltdns.net/manifest/v1/hls/v3/clear/"
            "665003303001/9291e7de-8b5b-4b92-9823-7b3cc3d5475c/10s/master.m3u8"
            "?fastly_token=NmE5MTAwZWZfNGI2OTdiNTQyZmE3ZTU3MDM1YzViMTRjOWJiNzk1MDkzZDY4YjRlZjcxMTZjNzY3NGY5ZDU1MTUwM2NhYjM2NA%3D%3D"
        )
        assert is_expiring_signed_video_url(url) is True

    def test_boltdns_url_without_token_is_not_expiring(self):
        assert is_expiring_signed_video_url("https://manifest.prod.boltdns.net/manifest/v1/hls/v3/clear/1/2/master.m3u8") is False

    def test_ordinary_video_url_is_not_expiring(self):
        assert is_expiring_signed_video_url("https://example.com/video.mp4") is False

    def test_none_url_is_not_expiring(self):
        assert is_expiring_signed_video_url(None) is False


class TestExtractVideoObjectMedia:
    @staticmethod
    def _page(*ld_blocks: str) -> str:
        return "<html><head>" + "".join(
            f'<script type="application/ld+json">{b}</script>' for b in ld_blocks
        ) + "</head><body></body></html>"

    def test_direct_mp4_content_url_is_returned(self):
        html = self._page(
            '{"@type": "NewsArticle", "headline": "x"}',
            '{"@type": "VideoObject", "contentUrl": "https://media.nw18.com/a/720p-h264.mp4",'
            ' "embedUrl": "https://media.nw18.com/a/720p-h264.mp4"}',
        )
        assert _extract_video_object_media(html) == "https://media.nw18.com/a/720p-h264.mp4"

    def test_ignores_related_video_urls_elsewhere_on_the_page(self):
        html = self._page(
            '{"@type": "VideoObject", "contentUrl": "https://media.nw18.com/own/720p-h264.mp4"}'
        ) + '<a href="https://media.nw18.com/other/manifest.m3u8">related</a>'
        assert _extract_video_object_media(html) == "https://media.nw18.com/own/720p-h264.mp4"

    def test_non_media_content_url_is_ignored(self):
        html = self._page('{"@type": "VideoObject", "contentUrl": "https://www.youtube.com/watch?v=abcdefghijk"}')
        assert _extract_video_object_media(html) is None

    def test_page_without_video_object_returns_none(self):
        assert _extract_video_object_media(self._page('{"@type": "NewsArticle"}')) is None
