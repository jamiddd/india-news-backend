"""
One-off migration for existing deployments: adds the `content` column to
the `articles` table. Needed because this project doesn't run real Alembic
migrations (no versions/ dir) — the app only does `Base.metadata.create_all`
on startup, which creates missing *tables* but never alters existing ones.

Safe to run multiple times (IF NOT EXISTS).

Usage:
    python3 scripts/add_content_column.py
"""
import asyncio
import logging
import os
import sys

# Ensure root of repo/backend is in sys.path (matches other scripts/ here)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import text

from app.database import engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main():
    async with engine.begin() as conn:
        await conn.execute(text("ALTER TABLE articles ADD COLUMN IF NOT EXISTS content TEXT"))
    logger.info("articles.content column is present.")


if __name__ == "__main__":
    asyncio.run(main())
