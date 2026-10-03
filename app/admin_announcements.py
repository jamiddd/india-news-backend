"""Admin control for the top-of-app banner — see app/models.py's
Announcement and the public GET /announcements/active endpoint in
app/main.py. Shows events, special offers or extra info at the top of the
app for a scheduled window, without a client release.

JSON API for the admin SPA's Announcements page (app/static/admin/sections/
announcements.js). Uses the shared app.admin_session cookie like every other
section; reads need require_admin, writes require_admin_write (CSRF header).

Several rows can be active at once; the app shows them one at a time,
highest priority first. Rows auto-expire at their end time, so the list only
shows what is live or still to come — there is nothing to clean up.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_session import require_admin, require_admin_write
from app.database import get_db
from app.models import Announcement, utc_now
from app.redis_client import get_redis_client

KINDS = ("event", "offer", "info")
ACTION_TYPES = ("", "url", "story", "paywall")
TITLE_MAX, BODY_MAX, CTA_MAX, ACTION_VALUE_MAX = 120, 240, 40, 500

# The admin form works in IST (fixed +05:30, no DST); storage stays UTC.
IST = timezone(timedelta(hours=5, minutes=30))

router = APIRouter(prefix="/admin/api/announcements", dependencies=[Depends(require_admin)])


async def _invalidate_active_cache() -> None:
    # GET /announcements/active caches under this key (see app/main.py) —
    # without this, an admin's add/delete wouldn't be visible in the app
    # until the short cache TTL expires. Same fail-open-on-error convention
    # as _cache_get/_cache_set: caching is a perf optimization, never a
    # correctness dependency.
    try:
        await get_redis_client().delete("announcements:active")
    except Exception:
        pass


def _utc(dt: datetime) -> datetime:
    # SQLite (tests) hands back naive datetimes; they are UTC.
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _parse_local_dt(raw: str, label: str) -> datetime:
    # The form's <input type=datetime-local> sends "YYYY-MM-DDTHH:MM" with no
    # timezone. Interpreted as IST, then converted to UTC for storage in
    # Announcement.starts_at/ends_at's DateTime(timezone=True) columns.
    try:
        return datetime.fromisoformat(raw).replace(tzinfo=IST).astimezone(timezone.utc)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{label} must be a date and time (YYYY-MM-DDTHH:MM, IST).")


def _status(row: Announcement, now: datetime) -> str:
    starts, ends = _utc(row.starts_at), _utc(row.ends_at)
    if starts <= now < ends:
        return "active"
    return "upcoming" if starts > now else "expired"


def _out(row: Announcement, now: datetime) -> dict:
    return {
        "id": row.id,
        "kind": row.kind,
        "title": row.title,
        "body": row.body,
        "ctaLabel": row.cta_label,
        "actionType": row.action_type,
        "actionValue": row.action_value,
        "startsAt": _utc(row.starts_at).isoformat(),
        "endsAt": _utc(row.ends_at).isoformat(),
        "priority": row.priority,
        "status": _status(row, now),
        "createdAt": _utc(row.created_at).isoformat() if row.created_at else None,
    }


@router.get("")
async def list_announcements(db: AsyncSession = Depends(get_db)):
    now = utc_now()
    rows = (await db.execute(
        select(Announcement).where(Announcement.ends_at >= now)
        .order_by(Announcement.starts_at, Announcement.priority.desc())
    )).scalars().all()
    return {
        "now": now.isoformat(),
        "kinds": list(KINDS),
        "actionTypes": [a for a in ACTION_TYPES if a],
        "items": [_out(r, now) for r in rows],
    }


class AnnouncementIn(BaseModel):
    kind: str = "info"
    title: str = ""
    body: str | None = None
    ctaLabel: str | None = None
    actionType: str | None = None
    actionValue: str | None = None
    startsAt: str = ""  # "YYYY-MM-DDTHH:MM" in IST
    endsAt: str = ""
    priority: int = 0


def _clean(value: str | None, label: str, limit: int) -> str | None:
    value = (value or "").strip()
    if len(value) > limit:
        raise HTTPException(status_code=400, detail=f"{label} can be at most {limit} characters.")
    return value or None


@router.post("", dependencies=[Depends(require_admin_write)])
async def add_announcement(body: AnnouncementIn, db: AsyncSession = Depends(get_db)):
    if body.kind not in KINDS:
        raise HTTPException(status_code=400, detail=f"Kind must be one of: {', '.join(KINDS)}.")
    title = _clean(body.title, "Title", TITLE_MAX)
    if not title:
        raise HTTPException(status_code=400, detail="Give the announcement a title.")
    action_type = body.actionType or ""
    if action_type not in ACTION_TYPES:
        raise HTTPException(status_code=400, detail="Action must be none, url, story or paywall.")
    starts_at = _parse_local_dt(body.startsAt, "Start")
    ends_at = _parse_local_dt(body.endsAt, "End")
    if ends_at <= starts_at:
        raise HTTPException(status_code=400, detail="The end has to be after the start.")

    row = Announcement(
        kind=body.kind,
        title=title,
        body=_clean(body.body, "Body", BODY_MAX),
        cta_label=_clean(body.ctaLabel, "Button label", CTA_MAX),
        action_type=action_type or None,
        action_value=_clean(body.actionValue, "Action value", ACTION_VALUE_MAX),
        starts_at=starts_at,
        ends_at=ends_at,
        priority=body.priority,
    )
    db.add(row)
    await db.commit()
    await _invalidate_active_cache()
    return {"ok": True, "item": _out(row, utc_now())}


@router.delete("/{announcement_id}", dependencies=[Depends(require_admin_write)])
async def delete_announcement(announcement_id: int, db: AsyncSession = Depends(get_db)):
    row = await db.get(Announcement, announcement_id)
    if row is None:
        raise HTTPException(status_code=404, detail="That announcement is already gone.")
    await db.delete(row)
    await db.commit()
    await _invalidate_active_cache()
    return {"ok": True, "id": announcement_id}
