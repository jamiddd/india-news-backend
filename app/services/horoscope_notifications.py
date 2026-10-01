"""Morning horoscope push: once a day, ~07:00 IST, notify every user who has
chosen a zodiac sign with a snippet of today's forecast, deep-linking to the
Horoscope screen. Driven from scripts/send_notifications.py, alongside
story_updates/topic_updates/source_updates — isolated the same way, so a
failure here can't undo or mask the sends before it.

Dedup is a plain (user, forecast_date) claim via HoroscopeNotification,
unique-indexed — see its docstring in app/models.py for why NotificationLog
doesn't fit. Unlike the follow-driven pushes above, this isn't opt-in by
following anything: it fires for anyone with zodiac_sign set AND
horoscope_notifications_enabled explicitly turned on (default off — the
client always sends some zodiac_sign once any settings sync happens, even
for a user who never chose one, so zodiac_sign alone isn't a safe signal of
real interest).
"""
import logging
from datetime import datetime, time
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import selectinload

from app.models import DailyHoroscope, HoroscopeNotification, User

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")

# Horoscopes prewarm at 06:00 IST (see scripts/run_crossword_scheduler.py's
# horoscope_loop) — start an hour later so today's forecast is reliably
# ready, and stop by late morning so a delayed run (outage, restart) never
# sends a "morning" push in the afternoon.
SEND_FROM = time(7, 0)
SEND_UNTIL = time(11, 0)

HOROSCOPE_CHANNEL = "horoscope"
MODE = "horoscope"

MAX_BODY_LEN = 140

# (device_token, title, body, cluster_id, channel_id, extra) -> delivered?
SendFn = Callable[[str, str, str, int, str, dict], Awaitable[bool]]


def build_title(sign: str) -> str:
    return f"{sign.capitalize()} · Today"


def build_body(forecast: dict, max_len: int = MAX_BODY_LEN) -> str:
    """A short excerpt of today's general-theme text, trimmed at a word
    boundary with an ellipsis rather than cut mid-word."""
    general = ((forecast or {}).get("horoscope") or {}).get("general") or ""
    general = general.strip()
    if len(general) <= max_len:
        return general
    truncated = general[:max_len].rsplit(" ", 1)[0].rstrip(".,;: ")
    return f"{truncated}…"


async def send_horoscope_notifications(session, now: datetime, send: SendFn) -> int:
    now_ist = now.astimezone(IST)
    if not (SEND_FROM <= now_ist.time() < SEND_UNTIL):
        return 0

    today = now_ist.date()
    forecasts = {
        row.sign: row.forecast
        for row in (
            await session.execute(
                select(DailyHoroscope).where(DailyHoroscope.forecast_date == today)
            )
        ).scalars().all()
    }
    if not forecasts:
        # Prewarm hasn't landed today's forecasts yet (or is down); a later
        # run within the send window will pick this up once it does.
        return 0

    users = (
        await session.execute(
            select(User)
            .options(selectinload(User.device_tokens))
            .where(
                text("preferences->>'zodiac_sign' IS NOT NULL"),
                # Opt-in, unlike the other switches' "IS NOT FALSE" pattern —
                # see the module docstring for why a bare zodiac_sign isn't
                # itself a reliable opt-in signal here.
                text("(preferences->>'horoscope_notifications_enabled')::boolean IS TRUE"),
            )
        )
    ).scalars().unique().all()

    sent = 0
    for user in users:
        if not user.device_tokens:
            continue
        sign = (user.preferences or {}).get("zodiac_sign")
        forecast = forecasts.get(sign)
        if forecast is None:
            # Either an unrecognised sign value or today's forecast for it
            # hasn't landed yet — either way, nothing to send this run.
            continue

        # Claim first, then push (see send_notifications._claim's
        # docstring): the unique (user, forecast_date) index is what
        # actually guarantees two overlapping runs can't both send today's
        # push to the same user.
        claimed = await session.execute(
            pg_insert(HoroscopeNotification)
            .values(user_id=user.id, forecast_date=today, sent_at=now)
            .on_conflict_do_nothing()
        )
        await session.commit()
        if claimed.rowcount == 0:
            continue

        title = build_title(sign)
        body = build_body(forecast)
        for device in list(user.device_tokens):
            ok = await send(
                device.fcm_token, title, body, 0, HOROSCOPE_CHANNEL,
                {"type": "horoscope", "sign": sign},
            )
            if not ok:
                await session.delete(device)
        await session.commit()
        sent += 1
    return sent
