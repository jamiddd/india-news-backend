"""Donations: captured-payment totals, a monthly series and the recent
payments, for the admin SPA's Donations page (app/static/admin/sections/
donations.js).

Read-only by design, same as the Donation model itself (see its docstring):
this page exists to answer "is the demand signal moving", not to let anyone
act on a donation from the admin. (/admin/engagement in app/main.py is the
other half of that signal and stays a separate JSON route.)
"""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_session import require_admin
from app.database import get_db
from app.models import Donation, User, utc_now
from app.services.polls import IST

router = APIRouter(prefix="/admin/api/donations", dependencies=[Depends(require_admin)])

LIST_LIMIT = 500
MONTHS = 12


@router.get("")
async def donations(db: AsyncSession = Depends(get_db)):
    now = utc_now()

    async def totals(*where):
        count, paise = (await db.execute(
            select(func.count(Donation.id), func.coalesce(func.sum(Donation.amount_paise), 0))
            .where(Donation.status == "captured", *where)
        )).one()
        return {"count": count, "inr": round((paise or 0) / 100, 2)}

    all_time = await totals()
    last_30 = await totals(Donation.created_at >= now - timedelta(days=30))
    distinct_donors = (await db.execute(
        select(func.count(func.distinct(Donation.user_id)))
        .where(Donation.status == "captured", Donation.user_id.isnot(None))
    )).scalar_one()

    # Grouped in Python rather than with date_trunc so the same code runs on
    # SQLite in tests; a year of captured donations is a small set.
    local = now.astimezone(IST)
    index = local.year * 12 + local.month - 1 - (MONTHS - 1)
    start_month = local.replace(year=index // 12, month=index % 12 + 1, day=1, hour=0, minute=0, second=0, microsecond=0)
    captured = (await db.execute(
        select(Donation.created_at, Donation.amount_paise)
        .where(Donation.status == "captured", Donation.created_at >= start_month)
    )).all()
    by_month: dict[str, int] = defaultdict(int)
    for created_at, paise in captured:
        moment = created_at if created_at.tzinfo else created_at.replace(tzinfo=now.tzinfo)
        by_month[moment.astimezone(IST).strftime("%Y-%m")] += paise
    months = []
    for i in range(index, index + MONTHS):
        key = f"{i // 12:04d}-{i % 12 + 1:02d}"
        months.append({"month": key, "inr": round(by_month.get(key, 0) / 100, 2)})

    rows = (await db.execute(
        select(Donation, User.display_name, User.email)
        .outerjoin(User, User.id == Donation.user_id)
        .order_by(Donation.created_at.desc())
        .limit(LIST_LIMIT)
    )).all()

    return {
        "allTime": {**all_time, "donors": distinct_donors},
        "last30Days": last_30,
        "months": months,
        "items": [{
            "id": d.id,
            "createdAt": d.created_at.isoformat() if d.created_at else None,
            "inr": round(d.amount_paise / 100, 2),
            "currency": d.currency,
            "provider": d.provider,
            "status": d.status,
            "userId": d.user_id,
            "donorName": name,
            "donorEmail": email,
        } for d, name, email in rows],
    }
