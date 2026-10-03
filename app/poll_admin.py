"""Human review gate for the AI-drafted daily poll.

JSON API for the admin SPA's Poll of the Day page (app/static/admin/sections/
polls.js). Sign-in is shared with every other admin section (app/admin_session.py)
so one login and one notification cover both of the reviewer's daily tasks
(poll and quiz). Reads need require_admin, writes require_admin_write (CSRF
header).

The reviewer sees today's draft, can edit question/context/options and approve
it for 9:00 AM IST, regenerate it, or reject it so activate_poll() publishes
from the poll bank instead. Once the draft is approved, rejected or past its
publish time it can no longer be edited.

Generation calls an LLM and can take up to a minute; it's a single POST the
SPA awaits. A failed generation comes back as a 502 whose detail is the
human-readable reason, which the SPA shows inline and in a toast.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_session import require_admin, require_admin_write
from app.database import get_db
from app.models import DailyPoll, PollOption
from app.services.polls import IST, approve_poll, generate_draft

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/api/polls", dependencies=[Depends(require_admin)])


def _aware(value: datetime | None) -> datetime | None:
    # Postgres returns timezone-aware values; SQLite (tests) returns naive UTC.
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _iso(value: datetime | None) -> str | None:
    value = _aware(value)
    return value.isoformat() if value else None


async def _today_poll(db: AsyncSession) -> DailyPoll | None:
    return await db.scalar(select(DailyPoll).where(DailyPoll.poll_date == datetime.now(IST).date()))


async def _serialize(db: AsyncSession, poll: DailyPoll) -> dict:
    options = (await db.execute(
        select(PollOption).where(PollOption.poll_id == poll.id).order_by(PollOption.position))).scalars().all()
    publish_at = _aware(poll.publish_at)
    return {
        "id": poll.id,
        "date": poll.poll_date.isoformat(),
        "question": poll.question,
        "context": poll.context,
        "options": [o.text for o in options],
        "status": poll.status,
        "generationMethod": poll.generation_method,
        "sourceClusterId": poll.source_cluster_id,
        "sourceHeadline": poll.source_headline,
        "sourceUrl": f"/api/v1/clusters/{poll.source_cluster_id}" if poll.source_cluster_id else None,
        "publishAt": _iso(poll.publish_at),
        "closesAt": _iso(poll.closes_at),
        "approvedAt": _iso(poll.approved_at),
        "createdAt": _iso(poll.created_at),
        "editable": poll.status == "draft" and publish_at is not None and datetime.now(IST) < publish_at,
    }


async def _state(db: AsyncSession) -> dict:
    poll = await _today_poll(db)
    return {
        "today": datetime.now(IST).date().isoformat(),
        "poll": await _serialize(db, poll) if poll else None,
    }


@router.get("")
async def today(db: AsyncSession = Depends(get_db)):
    return await _state(db)


async def _generate(db: AsyncSession, replace: bool) -> None:
    try:
        await generate_draft(db, datetime.now(IST).date(), replace=replace)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Daily poll draft generation failed: %s", exc)
        await db.rollback()
        raise HTTPException(status_code=502, detail=f"Draft generation failed: {exc}") from exc


# Static routes first, so "/generate" never matches "/{poll_id}".
@router.post("/generate", dependencies=[Depends(require_admin_write)])
async def generate(db: AsyncSession = Depends(get_db)):
    """Create today's draft when none exists (returns the existing one if it does)."""
    await _generate(db, replace=False)
    return await _state(db)


@router.post("/regenerate", dependencies=[Depends(require_admin_write)])
async def regenerate(db: AsyncSession = Depends(get_db)):
    """Replace today's draft with a fresh one. Only a draft can be regenerated."""
    await _generate(db, replace=True)
    return await _state(db)


class ApproveIn(BaseModel):
    question: str = ""
    context: str = ""
    options: list[str] = Field(default_factory=list, max_length=10)


@router.post("/{poll_id}/approve", dependencies=[Depends(require_admin_write)])
async def approve(poll_id: int, body: ApproveIn, db: AsyncSession = Depends(get_db)):
    # Blank option slots are dropped; validate_draft enforces the 2-4 count.
    options = [o.strip() for o in body.options if o.strip()]
    try:
        await approve_poll(db, poll_id, body.question, body.context, options)
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=f"{exc}.") from exc
    return await _state(db)


@router.post("/{poll_id}/reject", dependencies=[Depends(require_admin_write)])
async def reject(poll_id: int, db: AsyncSession = Depends(get_db)):
    """Reject the draft; activate_poll() then publishes from the poll bank."""
    poll = await db.get(DailyPoll, poll_id)
    if not poll or poll.status != "draft":
        raise HTTPException(status_code=409, detail="This draft can no longer be rejected")
    poll.status = "rejected"
    await db.commit()
    return await _state(db)
