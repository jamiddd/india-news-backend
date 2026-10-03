"""Admin control for manually-pushed "hot topic" tabs — see app/models.py's
AdminTopic and the public GET /topics/active endpoint in app/main.py. A
guaranteed-visibility lever for a story the algorithmic feed hasn't caught
up to yet: whatever word an admin adds here becomes its own tab to the
left of "For You" in the app, for that calendar date only (India calendar),
ordered by display_order. Rows auto-expire the next day.

Not to be confused with reader-followed topics: these are admin-pushed.

JSON API for the admin SPA's Hot topics page (app/static/admin/sections/
topics.js). Uses the shared app.admin_session cookie like every other
section; reads need require_admin, writes require_admin_write (CSRF header).
"""
from __future__ import annotations

from datetime import date, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_session import require_admin, require_admin_write
from app.database import get_db
from app.models import AdminTopic
from app.redis_client import get_redis_client
from app.services.crossword import india_today

WORD_MAX = 60

router = APIRouter(prefix="/admin/api/topics", dependencies=[Depends(require_admin)])


async def _invalidate_active_cache() -> None:
    # GET /topics/active caches under this key (see app/main.py) for
    # CACHE_TTL_SECONDS — without this, an admin's add/delete wouldn't be
    # visible in the app for up to 5 minutes. Same fail-open-on-error
    # convention as _cache_get/_cache_set: caching is a perf optimization,
    # never a correctness dependency.
    try:
        await get_redis_client().delete("topics:active")
    except Exception:
        pass


def _out(row: AdminTopic) -> dict:
    created = row.created_at
    if created is not None and created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return {
        "id": row.id,
        "date": row.topic_date.isoformat(),
        "word": row.word,
        "order": row.display_order,
        "createdAt": created.isoformat() if created else None,
    }


@router.get("")
async def list_topics(db: AsyncSession = Depends(get_db)):
    today = india_today()
    rows = (await db.execute(
        select(AdminTopic).where(AdminTopic.topic_date >= today)
        .order_by(AdminTopic.topic_date, AdminTopic.display_order, AdminTopic.id)
    )).scalars().all()
    return {"today": today.isoformat(), "items": [_out(r) for r in rows]}


class TopicIn(BaseModel):
    date: str = ""  # YYYY-MM-DD, India calendar
    word: str = ""
    order: int = 0


@router.post("", dependencies=[Depends(require_admin_write)])
async def add_topic(body: TopicIn, db: AsyncSession = Depends(get_db)):
    word = body.word.strip()
    if not word:
        raise HTTPException(status_code=400, detail="Type the word for the tab.")
    if len(word) > WORD_MAX:
        raise HTTPException(status_code=400, detail=f"Keep the word under {WORD_MAX} characters.")
    try:
        topic_date = date.fromisoformat(body.date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Pick a date (YYYY-MM-DD).")

    row = AdminTopic(topic_date=topic_date, word=word, display_order=body.order)
    db.add(row)
    await db.commit()
    await _invalidate_active_cache()
    return {"ok": True, "item": _out(row)}


@router.delete("/{topic_id}", dependencies=[Depends(require_admin_write)])
async def delete_topic(topic_id: int, db: AsyncSession = Depends(get_db)):
    row = await db.get(AdminTopic, topic_id)
    if row is None:
        raise HTTPException(status_code=404, detail="That topic is already gone.")
    await db.delete(row)
    await db.commit()
    await _invalidate_active_cache()
    return {"ok": True, "id": topic_id}
