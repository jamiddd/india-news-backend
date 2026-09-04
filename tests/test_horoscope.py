import contextlib
from datetime import date
from types import SimpleNamespace

import pytest

from fastapi import HTTPException

from app.services import horoscope
from app.services.horoscope import _normalise


def response_payload():
    return {
        "date": "2026-08-09",
        "sign": "Leo",
        "color": "Sapphire",
        "colorHex": "#0F52BA",
        "compatibility": ["Aries", "Sagittarius"],
        "luckyNumber": 7,
        "luckyTime": "Late Afternoon",
        "mood": "Confident",
        "zodiac": {"element": "Fire", "symbol": "♌"},
        "horoscope": {key: f"{key} reading" for key in ("general", "career", "finance", "health", "romance")},
        "horoscopeScore": {key: 4 for key in ("general", "career", "finance", "health", "romance")},
    }


def test_normalises_astrojson_response():
    result = _normalise(response_payload(), date(2026, 8, 9))
    assert result["sign"] == "Leo"
    assert result["horoscope"]["career"] == "career reading"
    assert result["scores"]["romance"] == 4


def test_rejects_wrong_provider_date():
    with pytest.raises(ValueError, match="date"):
        _normalise(response_payload(), date(2026, 8, 10))


def test_rejects_incomplete_horoscope():
    payload = response_payload()
    del payload["horoscope"]["health"]
    with pytest.raises(ValueError, match="themes"):
        _normalise(payload, date(2026, 8, 9))


def test_tolerates_missing_provider_date():
    payload = response_payload()
    del payload["date"]
    result = _normalise(payload, date(2026, 8, 9))
    assert result["mood"] == "Confident"


class FakeSession:
    """Only what get_or_create_horoscope touches outside the patched helpers."""

    def __init__(self):
        self.rolled_back = False
        self.committed = False

    async def execute(self, *args, **kwargs):
        return None

    async def rollback(self):
        self.rolled_back = True

    async def commit(self):
        self.committed = True

    async def refresh(self, row):
        return row


@pytest.fixture
def provider_down(monkeypatch):
    """A configured provider whose fetch always reports "not this day yet"."""
    monkeypatch.setattr(horoscope.settings, "ASTROJSON_API_KEY", "test-key")
    monkeypatch.setattr(horoscope.settings, "HOROSCOPE_ENABLED", True)
    horoscope._cooldown_until.clear()

    async def _not_ready(sign, forecast_date):
        raise horoscope.ProviderNotReady("provider is a day behind")

    monkeypatch.setattr(horoscope, "_fetch", _not_ready)
    monkeypatch.setattr(horoscope, "_exact", _none)
    yield monkeypatch
    horoscope._cooldown_until.clear()


async def _none(session, forecast_date, sign):
    return None


@pytest.mark.asyncio
async def test_serves_yesterday_when_provider_has_not_rolled_over(provider_down):
    stale = SimpleNamespace(forecast_date=date(2026, 8, 9), sign="leo")

    async def _stale(session, forecast_date, sign):
        return stale

    provider_down.setattr(horoscope, "_latest_recent", _stale)
    session = FakeSession()
    result = await horoscope.get_or_create_horoscope(session, date(2026, 8, 10), "leo")
    assert result is stale
    assert session.rolled_back
    assert not session.committed


@pytest.mark.asyncio
async def test_raises_when_nothing_recent_to_fall_back_to(provider_down):
    provider_down.setattr(horoscope, "_latest_recent", _none)
    with pytest.raises(HTTPException) as excinfo:
        await horoscope.get_or_create_horoscope(FakeSession(), date(2026, 8, 10), "leo")
    assert excinfo.value.status_code == 502


@pytest.mark.asyncio
async def test_prewarm_does_not_fall_back(provider_down):
    async def _unexpected(session, forecast_date, sign):
        raise AssertionError("prewarm must not serve a stale forecast")

    provider_down.setattr(horoscope, "_latest_recent", _unexpected)
    with pytest.raises(HTTPException):
        await horoscope.get_or_create_horoscope(
            FakeSession(), date(2026, 8, 10), "leo", allow_fallback=False,
        )


@pytest.mark.asyncio
async def test_prewarm_skips_the_provider_when_another_droplet_holds_the_lease(monkeypatch):
    monkeypatch.setattr(horoscope.settings, "ASTROJSON_API_KEY", "test-key")
    monkeypatch.setattr(horoscope.settings, "HOROSCOPE_ENABLED", True)

    @contextlib.asynccontextmanager
    async def _lease_taken(name, ttl_seconds=None):
        yield False

    async def _boom(*args, **kwargs):
        raise AssertionError("must not call the provider without the lease")

    async def _stored(session_factory, forecast_date):
        return 12

    monkeypatch.setattr(horoscope, "job_lease", _lease_taken)
    monkeypatch.setattr(horoscope, "get_or_create_horoscope", _boom)
    monkeypatch.setattr(horoscope, "_count_stored", _stored)

    ready, total = await horoscope.prewarm_horoscopes(None, date(2026, 8, 10))
    assert (ready, total) == (12, 12)
