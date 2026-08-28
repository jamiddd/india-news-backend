"""Pure-logic tests for app/services/extractor.py's URL classifiers."""
from app.services.extractor import is_expiring_signed_video_url


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
