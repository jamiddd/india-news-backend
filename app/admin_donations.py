"""A browsable donations page, wrapping the JSON totals already exposed at
GET /admin/donations (app/main.py) plus a raw list of recent captures.

Read-only by design, same as the Donation model itself (see its docstring):
this page exists to answer "is the demand signal moving", not to let anyone
act on a donation from the admin.
"""
from __future__ import annotations

import html
from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_session import layout, nav, session_csrf
from app.database import get_db
from app.models import Donation, User, utc_now

router = APIRouter(prefix="/admin/donations")
TITLE = "Donations"

PAGE_SIZE = 50


@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    if not session_csrf(request):
        return RedirectResponse("/admin/login", status_code=303)

    cutoff = utc_now() - timedelta(days=30)

    async def totals(*where):
        count, paise = (await db.execute(
            select(func.count(Donation.id), func.coalesce(func.sum(Donation.amount_paise), 0))
            .where(Donation.status == "captured", *where)
        )).one()
        return count, round((paise or 0) / 100, 2)

    all_count, all_inr = await totals()
    month_count, month_inr = await totals(Donation.created_at >= cutoff)
    distinct_donors = (await db.execute(
        select(func.count(func.distinct(Donation.user_id)))
        .where(Donation.status == "captured", Donation.user_id.isnot(None))
    )).scalar_one()

    summary = (
        "<div class=task><h2>All time</h2>"
        f"<p class=meta>{all_count} donations · ₹{all_inr:,.2f} · {distinct_donors} distinct donors</p></div>"
        "<div class=task><h2>Last 30 days</h2>"
        f"<p class=meta>{month_count} donations · ₹{month_inr:,.2f}</p></div>"
    )

    rows = (await db.execute(
        select(Donation, User.display_name, User.email)
        .outerjoin(User, User.id == Donation.user_id)
        .order_by(Donation.created_at.desc())
        .limit(PAGE_SIZE)
    )).all()

    def _row(donation: Donation, name: str | None, email: str | None) -> str:
        who = f"{html.escape(name)} ({html.escape(email)})" if name else (
            "anonymous / signed-out" if donation.user_id is None else f"user {donation.user_id}")
        status = donation.status if donation.status == "captured" else f"<b class=danger>{donation.status}</b>"
        return (
            f"<tr><td>{donation.created_at:%Y-%m-%d %H:%M}</td>"
            f"<td>₹{donation.amount_paise / 100:,.2f}</td>"
            f"<td>{html.escape(donation.provider)}</td>"
            f"<td>{status}</td>"
            f"<td>{who}</td></tr>")

    table = (
        "<table style='width:100%;border-collapse:collapse'>"
        "<tr><th align=left>When (UTC)</th><th align=left>Amount</th>"
        "<th align=left>Provider</th><th align=left>Status</th><th align=left>Donor</th></tr>"
        + "".join(_row(donation, name, email) for donation, name, email in rows)
        + "</table>") if rows else "<p class=meta>No donations yet.</p>"

    return layout(TITLE, (
        f"<h1>Donations</h1>{nav('/admin/donations')}{summary}"
        f"<h2>Last {PAGE_SIZE}</h2>{table}"))
