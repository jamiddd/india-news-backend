"""What needs a reviewer's decision today: the poll and quiz drafts that
expire at 09:00 IST if nobody acts on them.

This used to render the admin landing page. That page is now the Today view
of the admin SPA (app/static/admin/, fed by GET /admin/api/overview in
app/admin_api.py); the helper below stays here because the morning push
(app/services/admin_notify.py) and that overview must never disagree about
what is outstanding.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DailyPoll, DailyQuiz


async def pending_reviews(db: AsyncSession, day) -> dict[str, dict]:
    """What is waiting on the reviewer for `day`.

    Shared with the notifier (app/services/admin_notify.py) so the push and the
    page can never disagree about what is outstanding.
    """
    poll = await db.scalar(select(DailyPoll).where(DailyPoll.poll_date == day))
    quiz = await db.scalar(select(DailyQuiz).where(DailyQuiz.puzzle_date == day))
    return {
        "poll": {
            "exists": poll is not None,
            "status": poll.status if poll else None,
            "waiting": bool(poll and poll.status == "draft"),
            "summary": poll.question if poll else "No draft was generated",
            "url": "/admin/polls",
        },
        "quiz": {
            "exists": quiz is not None,
            "status": quiz.status if quiz else None,
            "waiting": bool(quiz and quiz.status == "draft"),
            "summary": (
                f"{len(quiz.questions)} questions ({quiz.source})" if quiz
                else "No draft was generated"),
            "url": "/admin/quiz",
        },
    }
