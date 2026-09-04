"""The budget guards. APIVerve bills a credit per successful response and the
backend runs on a ~60-credit monthly allowance, so the difference between
"throttled, retry" and "out of credits, stop" is the difference between a
working month and a dead one.
"""
import httpx
import pytest

from app.services import apiverve_client
from app.services.apiverve_client import _quota_exhausted, call_apiverve


def _response(status, *, body=None, headers=None):
    return httpx.Response(
        status, json=body if body is not None else {}, headers=headers or {},
        request=httpx.Request("GET", "https://api.apiverve.com/v1/trivia"),
    )


class TestQuotaDetection:
    def test_credit_limit_message_is_quota_not_throttle(self):
        """The exact production 429 from 2026-09-04."""
        response = _response(429, headers={"x-api-remaining-credits": "-4", "x-rate-limit-remaining": "1"})
        assert _quota_exhausted("Monthly credit limit reached. Upgrade to a paid plan for more credits.", response)

    def test_negative_credit_header_alone_is_enough(self):
        assert _quota_exhausted("something else", _response(429, headers={"x-api-remaining-credits": "-4"}))

    def test_plain_rate_limit_is_not_quota(self):
        """A throttle with credits left must stay retryable — treating it as
        exhaustion would degrade every game for the rest of the day."""
        response = _response(429, headers={"x-api-remaining-credits": "800", "x-rate-limit-remaining": "0"})
        assert not _quota_exhausted("Rate limit exceeded", response)

    def test_missing_headers_are_not_assumed_exhausted(self):
        assert not _quota_exhausted("Too many requests", _response(429))


@pytest.fixture(autouse=True)
def _reset_credits():
    apiverve_client._remaining_credits = None
    yield
    apiverve_client._remaining_credits = None


class TestCreditFloor:
    @pytest.mark.asyncio
    async def test_calls_stop_once_the_balance_reaches_the_floor(self, monkeypatch):
        monkeypatch.setattr(apiverve_client.settings, "APIVERVE_API_KEY", "k")
        apiverve_client._remaining_credits = apiverve_client.CREDIT_FLOOR

        called = False

        class _Client:
            def __init__(self, **_):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def request(self, *_, **__):
                nonlocal called
                called = True
                raise AssertionError("should not have been called")

        monkeypatch.setattr(apiverve_client.httpx, "AsyncClient", _Client)
        assert await call_apiverve("trivia") is None
        assert not called, "a call was made below the credit floor"

    @pytest.mark.asyncio
    async def test_unknown_balance_still_allows_a_first_call(self, monkeypatch):
        """A freshly started worker has never seen a response. It must not
        treat that as exhaustion or it could never make its first call."""
        monkeypatch.setattr(apiverve_client.settings, "APIVERVE_API_KEY", "k")

        class _Client:
            def __init__(self, **_):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def request(self, *_, **__):
                return _response(200, body={"status": "ok", "data": {"question": "q"}},
                                 headers={"x-api-remaining-credits": "42"})

        monkeypatch.setattr(apiverve_client.httpx, "AsyncClient", _Client)
        assert await call_apiverve("trivia") == {"question": "q"}
        assert apiverve_client.remaining_credits() == 42

    @pytest.mark.asyncio
    async def test_quota_429_returns_immediately_without_retrying(self, monkeypatch):
        """Three backoff sleeps against a quota error that cannot clear was the
        old behaviour, and it hid the cause behind a generic 429 log."""
        monkeypatch.setattr(apiverve_client.settings, "APIVERVE_API_KEY", "k")
        attempts = 0

        class _Client:
            def __init__(self, **_):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def request(self, *_, **__):
                nonlocal attempts
                attempts += 1
                return _response(
                    429,
                    body={"status": "error", "error": "Monthly credit limit reached."},
                    headers={"x-api-remaining-credits": "-4"},
                )

        monkeypatch.setattr(apiverve_client.httpx, "AsyncClient", _Client)
        assert await call_apiverve("trivia") is None
        assert attempts == 1, f"quota 429 was retried {attempts} times"
