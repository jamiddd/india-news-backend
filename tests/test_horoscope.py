from datetime import date

import pytest

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
