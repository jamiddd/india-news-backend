"""Reading and triaging what people send through the /feedback endpoint: the
website's form and the Android app's feedback/support screens (`source` says
which). It is also the only support inbox: there is no separate ticket table.

JSON API for the admin SPA's Feedback inbox (app/static/admin/sections/
feedback.js), on the shared app.admin_session cookie. Reads need a session;
status changes also need the X-CSRF-Token header.

The list is paged and filtered by status rather than returning everything:
unlike the poll and quiz reviews, which are empty most of the time by
construction, this table only grows, and "everything ever sent" stops being a
useful screen about a week in.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_session import require_admin, require_admin_write
from app.database import get_db
from app.models import Feedback

router = APIRouter(prefix="/admin/api/feedback", dependencies=[Depends(require_admin)])

PAGE_SIZE = 25
MAX_PAGE_SIZE = 100

CATEGORY_LABELS = {
    "bug": "Something is broken",
    "story": "A story is wrong or misleading",
    "feature": "An idea or a request",
    "publisher": "Publisher — include or remove",
    "other": "Something else",
}

# The order they appear as tabs, and the only values a status change will write.
STATUSES = ("new", "read", "closed")


def _item(item: Feedback) -> dict:
    # The Android app pre-fills the email from the signed-in account when the
    # person leaves the field blank, so for source=android an email does not
    # mean they asked for a reply.
    return {
        "id": item.id,
        "category": item.category,
        "categoryLabel": CATEGORY_LABELS.get(item.category, item.category),
        "name": item.name,
        "email": item.email,
        "wantsReply": bool(item.email) and item.source != "android",
        "emailFromAccount": bool(item.email) and item.source == "android",
        "message": item.message,
        "source": item.source,
        "userId": item.user_id,
        "status": item.status,
        "createdAt": item.created_at.isoformat() if item.created_at else None,
    }


@router.get("")
async def list_feedback(status: str = "new", q: str = "", offset: int = 0, limit: int = PAGE_SIZE,
                        db: AsyncSession = Depends(get_db)):
    if status not in STATUSES and status != "all":
        status = "new"
    offset, limit = max(offset, 0), min(max(limit, 1), MAX_PAGE_SIZE)

    counts = dict((await db.execute(
        select(Feedback.status, func.count()).group_by(Feedback.status))).all())

    query = select(Feedback)
    if status != "all":
        query = query.where(Feedback.status == status)
    q = q.strip()
    if q:
        like = f"%{q}%"
        query = query.where(or_(Feedback.message.ilike(like), Feedback.email.ilike(like),
                                Feedback.name.ilike(like), Feedback.user_id == q))
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    items = (await db.execute(
        query.order_by(Feedback.created_at.desc(), Feedback.id.desc()).offset(offset).limit(limit)
    )).scalars().all()

    return {
        "counts": {s: counts.get(s, 0) for s in STATUSES},
        "total": total,
        "offset": offset,
        "items": [_item(i) for i in items],
    }


@router.get("/{feedback_id}")
async def get_feedback(feedback_id: int, db: AsyncSession = Depends(get_db)):
    item = await db.get(Feedback, feedback_id)
    if not item:
        raise HTTPException(status_code=404, detail="That message no longer exists.")
    return _item(item)


class StatusIn(BaseModel):
    status: Literal["new", "read", "closed"]


class BulkStatusIn(StatusIn):
    ids: list[int] = Field(min_length=1, max_length=500)


@router.post("/bulk", dependencies=[Depends(require_admin_write)])
async def set_status_many(body: BulkStatusIn, db: AsyncSession = Depends(get_db)):
    items = (await db.execute(select(Feedback).where(Feedback.id.in_(body.ids)))).scalars().all()
    for item in items:
        item.status = body.status
    await db.commit()
    return {"ok": True, "updated": len(items)}


@router.post("/{feedback_id}", dependencies=[Depends(require_admin_write)])
async def set_status(feedback_id: int, body: StatusIn, db: AsyncSession = Depends(get_db)):
    item = await db.get(Feedback, feedback_id)
    if not item:
        raise HTTPException(status_code=404, detail="That message no longer exists.")
    item.status = body.status
    await db.commit()
    return _item(item)
