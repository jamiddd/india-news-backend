"""
One-off migration: adds the review-workflow columns to daily_quizzes so the
Daily Quiz can go through the same draft -> approve -> publish gate the daily
poll already uses (see app/services/polls.py and app/quiz_admin.py).

  status       draft | approved | rejected   (default 'approved')
  publish_at   when an approved quiz becomes visible
  approved_at  when a human approved it

`status` defaults to 'approved' so every quiz already in the table keeps
serving exactly as it does today — without that default, running this
migration would hide every historical quiz behind an approval that never
happened. New rows are written as 'draft' explicitly by generate_quiz.

Safe to run multiple times (IF NOT EXISTS).

Usage:
    python3 scripts/add_quiz_review_columns.py
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import text

from app.database import engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

COLUMNS = [
    "status VARCHAR(20) NOT NULL DEFAULT 'approved'",
    "publish_at TIMESTAMPTZ NULL",
    "approved_at TIMESTAMPTZ NULL",
]


async def main():
    async with engine.begin() as conn:
        for column in COLUMNS:
            await conn.execute(text(
                f"ALTER TABLE daily_quizzes ADD COLUMN IF NOT EXISTS {column}"
            ))
            logger.info("daily_quizzes.%s is present.", column.split()[0])
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_daily_quizzes_status ON daily_quizzes (status)"
        ))
        logger.info("daily_quizzes.status index is present.")


if __name__ == "__main__":
    asyncio.run(main())
