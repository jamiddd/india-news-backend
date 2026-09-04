import logging
import time as _time
from datetime import date, timedelta

import httpx
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import DailyHoroscope
from app.services.job_lease import job_lease


logger = logging.getLogger(__name__)

SIGNS = {
    "aries", "taurus", "gemini", "cancer", "leo", "virgo",
    "libra", "scorpio", "sagittarius", "capricorn", "aquarius", "pisces",
}

# How far back we are willing to reach for a stale forecast when the provider
# has nothing for today. India rolls over 5.5h before UTC, so the provider is
# routinely a day behind us between 00:00 and 05:30 IST; a couple of days of
# slack also covers a provider outage without the screen going blank.
MAX_FALLBACK_DAYS = 3

# After a failed provider call we stop asking for a while and serve the stale
# row instead, so a night-long outage is one call per sign per 5 minutes
# rather than one per user request.
_PROVIDER_COOLDOWN_SECONDS = 300
_cooldown_until: dict[tuple[date, str], float] = {}


class ProviderNotReady(ValueError):
    """The provider answered, but for a different day than we asked for."""


def _normalise(payload: dict, expected_date: date | None = None) -> dict:
    zodiac = payload.get("zodiac") or {}
    themes = payload.get("horoscope") or {}
    scores = payload.get("horoscopeScore") or {}
    required_themes = ("general", "career", "finance", "health", "romance")
    payload_date = payload.get("date")
    # A missing date is tolerated — only a date that actively disagrees with the
    # one we asked for means the provider has not rolled over yet.
    if expected_date is not None and payload_date and payload_date != expected_date.isoformat():
        raise ProviderNotReady(
            f"AstroJson returned date {payload_date}, expected {expected_date.isoformat()}"
        )
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


async def _fetch(sign: str, forecast_date: date) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(
            "https://api.astrojson.com/v1/horoscopes",
            params={"sign": sign, "lang": "en", "date": forecast_date.isoformat(), "period": "daily"},
            headers={"X-API-KEY": settings.ASTROJSON_API_KEY},
        )
        response.raise_for_status()
        return _normalise(response.json(), forecast_date)


async def _latest_recent(session: AsyncSession, forecast_date: date, sign: str) -> DailyHoroscope | None:
    """The newest stored forecast for this sign within the fallback window."""
    result = await session.execute(
        select(DailyHoroscope)
        .where(
            DailyHoroscope.sign == sign,
            DailyHoroscope.forecast_date <= forecast_date,
            DailyHoroscope.forecast_date >= forecast_date - timedelta(days=MAX_FALLBACK_DAYS),
        )
        .order_by(DailyHoroscope.forecast_date.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _exact(session: AsyncSession, forecast_date: date, sign: str) -> DailyHoroscope | None:
    result = await session.execute(select(DailyHoroscope).where(
        DailyHoroscope.forecast_date == forecast_date,
        DailyHoroscope.sign == sign,
    ))
    return result.scalar_one_or_none()


async def get_or_create_horoscope(
    session: AsyncSession,
    forecast_date: date,
    sign: str,
    *,
    allow_fallback: bool = True,
) -> DailyHoroscope:
    """Today's forecast, or the most recent one we have if today's isn't ready.

    Pass allow_fallback=False when the caller wants to know whether today
    specifically could be fetched (the nightly prewarm does).
    """
    sign = sign.lower()
    if sign not in SIGNS:
        raise HTTPException(status_code=422, detail="Unknown zodiac sign")
    if not settings.HOROSCOPE_ENABLED:
        raise HTTPException(status_code=404, detail="Horoscope is currently disabled")

    existing = await _exact(session, forecast_date, sign)
    if existing:
        return existing

    async def _fallback_or_raise(status: int, detail: str) -> DailyHoroscope:
        # Releases the advisory lock if we took one before failing.
        await session.rollback()
        if allow_fallback:
            stale = await _latest_recent(session, forecast_date, sign)
            if stale:
                logger.info(
                    "Serving %s horoscope from %s in place of %s",
                    sign, stale.forecast_date, forecast_date,
                )
                return stale
        raise HTTPException(status_code=status, detail=detail)

    if not settings.ASTROJSON_API_KEY:
        return await _fallback_or_raise(503, "Horoscope provider is not configured")

    cooldown_key = (forecast_date, sign)
    if allow_fallback and _cooldown_until.get(cooldown_key, 0) > _time.monotonic():
        stale = await _latest_recent(session, forecast_date, sign)
        if stale:
            return stale

    # Serialises first-generation requests for this date/sign across workers.
    lock_key = 780000000 + (forecast_date.toordinal() * 20) + sorted(SIGNS).index(sign)
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
    existing = await _exact(session, forecast_date, sign)
    if existing:
        return existing

    try:
        forecast = await _fetch(sign, forecast_date)
    except ProviderNotReady as exc:
        # Expected every night between 00:00 and 05:30 IST — not an error.
        _cooldown_until[cooldown_key] = _time.monotonic() + _PROVIDER_COOLDOWN_SECONDS
        logger.info("Horoscope not ready for %s %s: %s", sign, forecast_date, exc)
        return await _fallback_or_raise(502, "Horoscope provider is temporarily unavailable")
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        _cooldown_until[cooldown_key] = _time.monotonic() + _PROVIDER_COOLDOWN_SECONDS
        logger.warning("Horoscope fetch failed for %s %s: %s", sign, forecast_date, exc)
        return await _fallback_or_raise(502, "Horoscope provider is temporarily unavailable")

    _cooldown_until.pop(cooldown_key, None)
    row = DailyHoroscope(forecast_date=forecast_date, sign=sign, forecast=forecast)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


# Twelve sequential fetches at a 20s timeout is 240s worst case, well past
# the lease default of 180s — a lease that expires mid-run would let the
# other droplet start a duplicate set of paid calls, which is the whole
# thing this lease exists to prevent.
_PREWARM_LEASE_TTL_SECONDS = 600


async def _count_stored(session_factory, forecast_date: date) -> int:
    async with session_factory() as session:
        return await session.scalar(
            select(func.count()).select_from(DailyHoroscope).where(
                DailyHoroscope.forecast_date == forecast_date
            )
        ) or 0


async def prewarm_horoscopes(session_factory, forecast_date: date) -> tuple[int, int]:
    """Fetch and store all twelve signs for a date. Never raises.

    Returns (stored, total). A partial result is normal when the provider has
    not rolled over to forecast_date yet — the caller is expected to try again
    later rather than treat it as a failure.

    Both droplets run this scheduler, so the work is taken under a lease: the
    row check inside get_or_create_horoscope would stop a duplicate *write*,
    but only after both runs had already paid for the HTTP call.
    """
    if not settings.HOROSCOPE_ENABLED or not settings.ASTROJSON_API_KEY:
        return (0, len(SIGNS))

    lease_name = f"horoscope_prewarm:{forecast_date.isoformat()}"
    async with job_lease(lease_name, ttl_seconds=_PREWARM_LEASE_TTL_SECONDS) as got_lease:
        if not got_lease:
            logger.info("Horoscope prewarm for %s is already running elsewhere", forecast_date)
            # Report what is actually on disk rather than a bare 0, so the
            # droplet that loses the race does not log a false failure.
            return (await _count_stored(session_factory, forecast_date), len(SIGNS))
        ready = 0
        for sign in sorted(SIGNS):
            try:
                async with session_factory() as session:
                    await get_or_create_horoscope(session, forecast_date, sign, allow_fallback=False)
                ready += 1
            except Exception as exc:  # noqa: BLE001 - a prewarm must never take the loop down
                logger.info("Horoscope prewarm skipped %s for %s: %s", sign, forecast_date, exc)
        return (ready, len(SIGNS))
