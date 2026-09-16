"""Brevo HTTP API send — see admin_email.py's module docstring for why this
isn't SMTP (DigitalOcean blocks outbound ports 25/465/587 account-wide)."""
import pytest

from app.config import settings
from app.services.admin_email import send_admin_email


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, "BREVO_API_KEY", "test-key")
    monkeypatch.setattr(settings, "BREVO_SENDER_EMAIL", "sender@example.com")
    monkeypatch.setattr(settings, "ADMIN_ALERT_EMAIL_TO", "admin@example.com")


@pytest.fixture
def capture_request(monkeypatch):
    """Records the outgoing call and returns a canned Brevo response."""
    sent = {}

    class FakeResponse:
        status_code = 201

        def raise_for_status(self):
            pass

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, headers=None, json=None):
            sent.update(url=url, headers=headers, body=json)
            return FakeResponse()

    monkeypatch.setattr("app.services.admin_email.httpx.AsyncClient", FakeClient)
    return sent


class TestSendAdminEmail:
    async def test_returns_false_when_not_configured(self):
        assert await send_admin_email("subject", "body") is False

    async def test_sends_via_brevo_api_with_key_header(self, configured, capture_request):
        result = await send_admin_email("Breaking review", "waiting for review")

        assert result is True
        assert capture_request["url"] == "https://api.brevo.com/v3/smtp/email"
        assert capture_request["headers"]["api-key"] == "test-key"
        assert capture_request["body"]["sender"] == {"email": "sender@example.com"}
        assert capture_request["body"]["to"] == [{"email": "admin@example.com"}]
        assert capture_request["body"]["subject"] == "Breaking review"
        assert capture_request["body"]["textContent"] == "waiting for review"

    async def test_returns_false_rather_than_raising_when_brevo_fails(self, configured, monkeypatch):
        class ExplodingClient:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, *args, **kwargs):
                raise RuntimeError("network down")

        monkeypatch.setattr("app.services.admin_email.httpx.AsyncClient", ExplodingClient)
        assert await send_admin_email("subject", "body") is False

    async def test_returns_false_when_only_partially_configured(self, monkeypatch):
        monkeypatch.setattr(settings, "BREVO_API_KEY", "test-key")
        monkeypatch.setattr(settings, "BREVO_SENDER_EMAIL", None)
        monkeypatch.setattr(settings, "ADMIN_ALERT_EMAIL_TO", "admin@example.com")
        assert await send_admin_email("subject", "body") is False
