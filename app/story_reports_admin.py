"""Reviewing user-submitted story reports (misleading/offensive/etc flags).

JSON API for the admin SPA's Story reports page (app/static/admin/sections/
reports.js). Uses the shared app.admin_session cookie like every other
section; reads need require_admin, writes require_admin_write (CSRF header).

A report is "open" until a reviewer marks it reviewed or dismisses it. The
SPA lists open ones by default and can show the closed ones too, which the old
HTML page never could.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_session import require_admin, require_admin_write
from app.database import get_db
from app.models import StoryCluster, StoryReport

router = APIRouter(prefix="/admin/api/reports", dependencies=[Depends(require_admin)])

REASON_LABELS = {
    "misleading": "Misleading / Clickbait",
    "factually_incorrect": "Factually incorrect",
    "offensive": "Offensive / Inappropriate",
    "duplicate_spam": "Duplicate / Spam",
    "other": "Other",
}
STATUSES = ("open", "reviewed", "dismissed")
LIST_LIMIT = 1000


def _target(report: StoryReport, headline: str | None) -> dict:
    if report.cluster_id:
        return {"kind": "story", "id": report.cluster_id, "title": headline or f"Story {report.cluster_id}",
                "url": f"/api/v1/clusters/{report.cluster_id}"}
    if report.timeline_feature_id:
        return {"kind": "timeline", "id": report.timeline_feature_id, "title": f"Timeline {report.timeline_feature_id}",
                "url": f"/api/v1/timelines/{report.timeline_feature_id}"}
    if report.explainer_id:
        return {"kind": "explainer", "id": report.explainer_id, "title": f"Explainer {report.explainer_id}",
                "url": f"/api/v1/explainers/{report.explainer_id}"}
    return {"kind": "deleted", "id": None, "title": "Content deleted", "url": None}


@router.get("")
async def list_reports(status: str = "open", db: AsyncSession = Depends(get_db)):
    counts = dict((await db.execute(
        select(StoryReport.status, func.count()).group_by(StoryReport.status))).all())
    query = (select(StoryReport, StoryCluster.headline)
             .outerjoin(StoryCluster, StoryCluster.id == StoryReport.cluster_id)
             .order_by(StoryReport.created_at.desc()).limit(LIST_LIMIT))
    if status in STATUSES:
        query = query.where(StoryReport.status == status)
    rows = (await db.execute(query)).all()
    return {
        "counts": {s: counts.get(s, 0) for s in STATUSES},
        "reasons": REASON_LABELS,
        "items": [{
            "id": r.id,
            "reason": r.reason,
            "reasonLabel": REASON_LABELS.get(r.reason, r.reason),
            "note": r.note,
            "userId": r.user_id,
            "status": r.status,
            "createdAt": r.created_at.isoformat() if r.created_at else None,
            "target": _target(r, headline),
        } for r, headline in rows],
    }


class DecideIn(BaseModel):
    action: Literal["reviewed", "dismissed", "open"]


class BulkDecideIn(DecideIn):
    ids: list[int] = Field(min_length=1, max_length=500)


@router.post("/bulk", dependencies=[Depends(require_admin_write)])
async def decide_many(body: BulkDecideIn, db: AsyncSession = Depends(get_db)):
    reports = (await db.execute(select(StoryReport).where(StoryReport.id.in_(body.ids)))).scalars().all()
    for report in reports:
        report.status = body.action
    await db.commit()
    return {"ok": True, "updated": len(reports)}


@router.post("/{report_id}", dependencies=[Depends(require_admin_write)])
async def decide(report_id: int, body: DecideIn, db: AsyncSession = Depends(get_db)):
    report = await db.get(StoryReport, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    report.status = body.action
    await db.commit()
    return {"ok": True, "id": report_id, "status": body.action}
