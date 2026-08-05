"""
One-off backfill: re-run clean_extracted_text() over every Article.content
already stored in the DB, in place.

Existing rows hold trafilatura's *raw* extraction (scraped before
content_cleaner.py existed, or before a given cleanup rule was added), so
re-cleaning them doesn't need a network fetch — just re-apply the current
cleaning rules to the text already in hand and update rows that changed.

Safe to re-run any time the cleaning rules change (idempotent: rows already
clean are left untouched).

Usage:
    python3 scripts/backfill_clean_content.py
"""
import asyncio
import logging
import os
import sys

# Ensure root of repo/backend is in sys.path (matches other scripts/ here)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select, update

from app.database import AsyncSessionLocal
from app.models import Article
from app.services.content_cleaner import clean_extracted_text

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main():
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Article.id, Article.title, Article.content).where(Article.content.isnot(None))
        )
        rows = result.all()
        logger.info(f"Found {len(rows)} articles with stored content to re-clean.")

        updated = 0
        for row in rows:
            cleaned = clean_extracted_text(row.content, row.title)
            if cleaned != row.content:
                await session.execute(
                    update(Article).where(Article.id == row.id).values(content=cleaned)
                )
                updated += 1

        await session.commit()
        logger.info(f"Done. Updated {updated} of {len(rows)} rows ({len(rows) - updated} were already clean).")


if __name__ == "__main__":
    asyncio.run(main())
