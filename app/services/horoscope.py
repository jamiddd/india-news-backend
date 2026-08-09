from datetime import date

import httpx
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import DailyHoroscope


SIGNS = {
    "aries", "taurus", "gemini", "cancer", "leo", "virgo",
    "libra", "scorpio", "sagittarius", "capricorn", "aquarius", "pisces",
}


def _normalise(payload: dict, expected_date: date | None = None) -> dict:
    zodiac = payload.get("zodiac") or {}
    themes = payload.get("horoscope") or {}
    scores = payload.get("horoscopeScore") or {}
    required_themes = ("general", "career", "finance", "health", "romance")
    if expected_date is not None and payload.get("date") != expected_date.isoformat():
        raise ValueError("AstroJson response date does not match the requested India date")
    if any(not isinstance(themes.get(key), str) or not themes[key].strip() for key in required_themes):
        raise ValueError("AstroJson response is missing horoscope themes")
    return {
        "sign": str(payload["sign"]),
        "symbol": str(zodiac.get("symbol") or ""),
        "element": str(zodiac.get("element") or ""),
        "color": str(payload.get("color") or ""),
        "color_hex": payload.get("colorHex"),
        "compatibility": [str(value) for value in payload.get("compatibility") or []],
        "lucky_number": int(payload.get("luckyNumber") or 0),
        "lucky_time": str(payload.get("luckyTime") or ""),
        "mood": str(payload.get("mood") or ""),
        "horoscope": {key: themes[key].strip() for key in required_themes},
        "scores": {key: max(0, min(5, int(scores.get(key) or 0))) for key in required_themes},
    }


async def get_or_create_horoscope(session: AsyncSession, forecast_date: date, sign: str) -> DailyHoroscope:
    sign = sign.lower()
    if sign not in SIGNS:
        raise HTTPException(status_code=422, detail="Unknown zodiac sign")
    if not settings.HOROSCOPE_ENABLED:
        raise HTTPException(status_code=404, detail="Horoscope is currently disabled")

    result = await session.execute(select(DailyHoroscope).where(
        DailyHoroscope.forecast_date == forecast_date,
        DailyHoroscope.sign == sign,
    ))
    existing = result.scalar_one_or_none()
    if existing:
        return existing
    if not settings.ASTROJSON_API_KEY:
        raise HTTPException(status_code=503, detail="Horoscope provider is not configured")

    # Serialises first-generation requests for this date/sign across workers.
    lock_key = 780000000 + (forecast_date.toordinal() * 20) + sorted(SIGNS).index(sign)
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
    result = await session.execute(select(DailyHoroscope).where(
        DailyHoroscope.forecast_date == forecast_date,
        DailyHoroscope.sign == sign,
    ))
    existing = result.scalar_one_or_none()
    if existing:
        return existing

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                "https://api.astrojson.com/v1/horoscopes",
                params={"sign": sign, "lang": "en", "date": forecast_date.isoformat(), "period": "daily"},
                headers={"X-API-KEY": settings.ASTROJSON_API_KEY},
            )
            response.raise_for_status()
            forecast = _normalise(response.json(), forecast_date)
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="Horoscope provider is temporarily unavailable") from exc

    row = DailyHoroscope(forecast_date=forecast_date, sign=sign, forecast=forecast)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row
