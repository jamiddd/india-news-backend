"""A searchable list of accounts, for support ("does this email have an
account") and abuse triage. Read-only: there is nothing here to edit, only to
look up. Deleting an account stays a user-initiated action through the app
(see DELETE /users/{user_id} in app/main.py), not something to expose here.
"""
from __future__ import annotations

import html

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_session import layout, nav, session_csrf
from app.database import get_db
from app.models import Donation, SavedStory, User

router = APIRouter(prefix="/admin/users")
TITLE = "Users"

PAGE_SIZE = 50


@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request, q: str = "", page: int = 1, db: AsyncSession = Depends(get_db)):
    if not session_csrf(request):
        return RedirectResponse("/admin/login", status_code=303)
    page = max(page, 1)
    q = q.strip()

    base = select(User)
    if q:
        like = f"%{q}%"
        base = base.where(or_(User.email.ilike(like), User.display_name.ilike(like), User.id == q))

    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    users = (await db.execute(
        base.order_by(User.created_at.desc()).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)
    )).scalars().all()

    donated = {}
    saved_counts = {}
    if users:
        ids = [u.id for u in users]
        donated = dict((await db.execute(
            select(Donation.user_id, func.coalesce(func.sum(Donation.amount_paise), 0))
            .where(Donation.user_id.in_(ids), Donation.status == "captured")
            .group_by(Donation.user_id)
        )).all())
        saved_counts = dict((await db.execute(
            select(SavedStory.user_id, func.count())
            .where(SavedStory.user_id.in_(ids)).group_by(SavedStory.user_id)
        )).all())

    def _row(user: User) -> str:
        donated_inr = donated.get(user.id, 0) / 100
        return (
            f"<tr><td>{html.escape(user.display_name)}<br>"
            f"<span class=meta>{html.escape(user.email)}</span></td>"
            f"<td>{html.escape(user.provider)}</td>"
            f"<td>{user.created_at:%Y-%m-%d}</td>"
            f"<td>{saved_counts.get(user.id, 0)}</td>"
            f"<td>{'₹%.2f' % donated_inr if donated_inr else '—'}</td>"
            f"<td><span class=meta>{html.escape(user.id)}</span></td></tr>")

    table = (
        "<table style='width:100%;border-collapse:collapse'>"
        "<tr><th align=left>User</th><th align=left>Provider</th><th align=left>Joined</th>"
        "<th align=left>Saved</th><th align=left>Donated</th><th align=left>ID</th></tr>"
        + "".join(_row(u) for u in users) + "</table>") if users else "<p class=meta>No matching users.</p>"

    pager = ""
    if page > 1:
        pager += f"<a href='/admin/users?q={html.escape(q)}&page={page - 1}'>← newer</a> "
    if total > page * PAGE_SIZE:
        pager += f"<a href='/admin/users?q={html.escape(q)}&page={page + 1}'>older →</a>"

    return layout(TITLE, (
        f"<h1>Users</h1>{nav('/admin/users')}"
        f"<p class=meta>{total} matching · <a href='/admin/users'>clear</a></p>"
        f"<form method=get><input name=q placeholder='email, name, or user id' "
        f"value='{html.escape(q, quote=True)}'><button>Search</button></form>"
        f"{table}<p>{pager}</p>"))
