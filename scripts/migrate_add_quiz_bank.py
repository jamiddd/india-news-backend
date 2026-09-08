"""
One-off migration adding the quiz_bank_questions table (see
app.models.QuizBankQuestion) — the admin-curated fallback bank for the Daily
Quiz, used by generate_quiz() when Claude's draft fails validation and by
/admin/quiz-bank for adding/listing questions by hand.

Safe to run multiple times — CREATE TABLE IF NOT EXISTS.

Usage:
    python3 scripts/migrate_add_quiz_bank.py
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


async def main():
    async with engine.begin() as conn:
        await conn.execute(text(
            """
            CREATE TABLE IF NOT EXISTS quiz_bank_questions (
                id SERIAL PRIMARY KEY,
                question TEXT NOT NULL,
                options JSON NOT NULL,
                correct_index INTEGER NOT NULL,
                explanation TEXT NOT NULL DEFAULT '',
                category VARCHAR(50),
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                used_count INTEGER NOT NULL DEFAULT 0,
                last_used_at TIMESTAMPTZ
            )
            """
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_quiz_bank_questions_is_active "
            "ON quiz_bank_questions (is_active)"
        ))
        logger.info("quiz_bank_questions table is present.")


if __name__ == "__main__":
    asyncio.run(main())
