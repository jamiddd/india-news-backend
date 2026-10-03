"""A searchable list of accounts, for support ("does this email have an
account") and abuse triage. Read-only: there is nothing here to edit, only to
look up. Deleting an account stays a user-initiated action through the app
(POST /api/v1/account/delete in app/main.py), not something to expose here.

JSON API for the admin SPA's Users page (app/static/admin/sections/users.js).
The table pages on the server (DataTables server-side mode): the users table
is far too large to ship to the browser whole.
"""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_session import require_admin
from app.database import get_db
from app.models import Donation, PremiumTrial, SavedStory, User, utc_now

router = APIRouter(prefix="/admin/api/users", dependencies=[Depends(require_admin)])

MAX_PAGE_SIZE = 100
SORTABLE = {"displayName": User.display_name, "email": User.email, "provider": User.provider,
            "createdAt": User.created_at}


@router.get("")
async def list_users(q: str = "", start: int = 0, length: int = 25, orderBy: str = "createdAt",
                     dir: str = "desc", db: AsyncSession = Depends(get_db)):
    start, length = max(start, 0), min(max(length, 1), MAX_PAGE_SIZE)
    q = q.strip()

    total = await db.scalar(select(func.count()).select_from(User)) or 0
    base = select(User)
    if q:
        like = f"%{q}%"
        base = base.where(or_(User.email.ilike(like), User.display_name.ilike(like), User.id == q))
    filtered = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one() if q else total

    column = SORTABLE.get(orderBy, User.created_at)
    users = (await db.execute(
        base.order_by(column.asc() if dir == "asc" else column.desc(), User.id)
        .offset(start).limit(length)
    )).scalars().all()

    donated, saved, trials = {}, {}, {}
    if users:
        ids = [u.id for u in users]
        donated = dict((await db.execute(
            select(Donation.user_id, func.coalesce(func.sum(Donation.amount_paise), 0))
            .where(Donation.user_id.in_(ids), Donation.status == "captured")
            .group_by(Donation.user_id)
        )).all())
        saved = dict((await db.execute(
            select(SavedStory.user_id, func.count())
            .where(SavedStory.user_id.in_(ids)).group_by(SavedStory.user_id)
        )).all())
        trials = {t.user_id: t for t in (await db.execute(
            select(PremiumTrial).where(PremiumTrial.user_id.in_(ids)))).scalars().all()}

    now = utc_now()

    def trial_state(user_id: str) -> str | None:
        t = trials.get(user_id)
        if not t:
            return None
        if t.converted_at:
            return "converted"
        ends = t.ends_at if t.ends_at.tzinfo else t.ends_at.replace(tzinfo=now.tzinfo)
        return "active" if ends > now else "ended"

    return {
        "total": total,
        "filtered": filtered,
        "rows": [{
            "id": u.id,
            "displayName": u.display_name,
            "email": u.email,
            "provider": u.provider,
            "photoUrl": u.photo_url,
            "createdAt": u.created_at.isoformat() if u.created_at else None,
            "saved": saved.get(u.id, 0),
            "donatedInr": round(donated.get(u.id, 0) / 100, 2),
            "trial": trial_state(u.id),
        } for u in users],
    }


@router.get("/stats")
async def stats(db: AsyncSession = Depends(get_db)):
    now = utc_now()
    total = await db.scalar(select(func.count()).select_from(User)) or 0
    week = await db.scalar(select(func.count()).select_from(User).where(User.created_at >= now - timedelta(days=7))) or 0
    trials_active = await db.scalar(select(func.count()).select_from(PremiumTrial).where(
        PremiumTrial.ends_at > now, PremiumTrial.converted_at.is_(None))) or 0
    trials_converted = await db.scalar(select(func.count()).select_from(PremiumTrial).where(
        PremiumTrial.converted_at.isnot(None))) or 0
    providers = dict((await db.execute(select(User.provider, func.count()).group_by(User.provider))).all())

    since = now - timedelta(days=30)
    days = (await db.execute(
        select(func.date(User.created_at), func.count()).where(User.created_at >= since)
        .group_by(func.date(User.created_at))
    )).all()
    by_day = {str(day)[:10]: n for day, n in days}
    series = [{"day": d, "count": by_day.get(d, 0)}
              for d in ((since + timedelta(days=i + 1)).date().isoformat() for i in range(30))]

    return {"total": total, "joinedThisWeek": week, "trialsActive": trials_active,
            "trialsConverted": trials_converted, "providers": providers, "signups": series}
