"""Admin CRUD for the Poll question bank (app.models.PollFallback).

JSON API for the admin SPA's Poll bank page (app/static/admin/sections/
polls.js). Separate from poll_admin.py deliberately, same reason quiz_admin.py
and quiz_bank_admin.py stay separate: that page reviews one AI-drafted day;
this one manages the standing bank that activate_poll() publishes from when no
draft was approved by 9:00 AM (least-recently-used first). Sign-in is shared
via app.admin_session, same as every other admin section; reads need
require_admin, writes require_admin_write (CSRF header).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_session import require_admin, require_admin_write
from app.database import get_db
from app.models import PollFallback
from app.services.polls import draft_bank_poll, seed_fallbacks, validate_draft

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/api/poll-bank", dependencies=[Depends(require_admin)])
MAX_OPTIONS = 4  # validate_draft accepts 2-4; the SPA requires the first two inputs
MAX_CATEGORY = 50  # PollFallback.category is VARCHAR(50)
AVOID_LIST_SIZE = 25  # existing questions shown to Claude so it doesn't repeat them
LIST_LIMIT = 2000


def _item(p: PollFallback) -> dict:
    return {
        "id": p.id,
        "question": p.question,
        "context": p.context,
        "options": list(p.options or []),
        "category": p.category,
        "active": bool(p.active),
        "usedCount": p.used_count or 0,
        "lastUsedAt": p.last_used_at.isoformat() if p.last_used_at else None,
        "createdAt": p.created_at.isoformat() if p.created_at else None,
    }


@router.get("")
async def list_bank(db: AsyncSession = Depends(get_db)):
    # Seed the built-in polls now rather than lazily on first activation, so
    # they show up here — and so adding a poll to a still-empty table can't
    # suppress the seed (seed_fallbacks skips a non-empty table).
    await seed_fallbacks(db)
    total = (await db.execute(select(func.count()).select_from(PollFallback))).scalar_one()
    active_total = (await db.execute(
        select(func.count()).where(PollFallback.active.is_(True)))).scalar_one()
    rows = (await db.execute(
        select(PollFallback).order_by(PollFallback.created_at.desc(), PollFallback.id.desc()).limit(LIST_LIMIT)
    )).scalars().all()
    return {
        "total": total,
        "active": active_total,
        "maxOptions": MAX_OPTIONS,
        "maxCategory": MAX_CATEGORY,
        "items": [_item(p) for p in rows],
    }


class GenerateIn(BaseModel):
    category: str | None = None


@router.post("/generate", dependencies=[Depends(require_admin_write)])
async def generate(body: GenerateIn, db: AsyncSession = Depends(get_db)):
    """Draft one poll with Claude and hand it back for the add form — never
    saved directly. Follows [[llm-generation-human-review-gate]]: generate
    greedily, let the human reviewer be the only gate before it's active in
    the bank."""
    category = (body.category or "").strip()[:MAX_CATEGORY] or None
    existing = (await db.execute(
        select(PollFallback.question).order_by(PollFallback.id.desc()).limit(AVOID_LIST_SIZE)
    )).scalars().all()
    try:
        result = await draft_bank_poll(category, list(existing))
    except ValueError as exc:
        logger.warning("Bank poll generation failed validation: %s", exc)
        raise HTTPException(status_code=502, detail=f"Claude's draft failed validation ({exc}). Try again.") from exc
    if result is None:
        raise HTTPException(status_code=502, detail="Claude request failed. Try again.")
    return {"draft": result}


class AddIn(BaseModel):
    question: str = ""
    context: str = ""
    options: list[str] = Field(default_factory=list, max_length=MAX_OPTIONS)
    category: str | None = None


@router.post("", dependencies=[Depends(require_admin_write)])
async def add(body: AddIn, db: AsyncSession = Depends(get_db)):
    # Blank optional inputs are dropped; validate_draft enforces the 2-4 count.
    options = [o.strip() for o in body.options if o and o.strip()]
    category = (body.category or "").strip() or None
    try:
        question, context, options = validate_draft(
            {"question": body.question, "context": body.context, "options": options})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{exc}.") from exc
    if category and len(category) > MAX_CATEGORY:
        raise HTTPException(status_code=422, detail=f"Category must be {MAX_CATEGORY} characters or fewer.")

    await seed_fallbacks(db)
    duplicate = await db.scalar(
        select(PollFallback.id).where(func.lower(PollFallback.question) == question.lower()).limit(1))
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="That question is already in the bank.")

    poll = PollFallback(question=question, context=context, options=options, category=category)
    db.add(poll)
    await db.commit()
    await db.refresh(poll)
    return {"ok": True, "item": _item(poll)}


async def _get(db: AsyncSession, poll_id: int) -> PollFallback:
    poll = await db.get(PollFallback, poll_id)
    if poll is None:
        raise HTTPException(status_code=404, detail="Poll not found")
    return poll


@router.post("/{poll_id}/toggle", dependencies=[Depends(require_admin_write)])
async def toggle(poll_id: int, db: AsyncSession = Depends(get_db)):
    poll = await _get(db, poll_id)
    poll.active = not poll.active
    await db.commit()
    return {"ok": True, "id": poll_id, "active": poll.active}


@router.delete("/{poll_id}", dependencies=[Depends(require_admin_write)])
async def delete(poll_id: int, db: AsyncSession = Depends(get_db)):
    poll = await _get(db, poll_id)
    await db.delete(poll)
    await db.commit()
    return {"ok": True, "id": poll_id}
