"""
One-off backfill: re-run clean_extracted_text() over every Article.snippet
already stored in the DB, in place.

Fixes the corruption left behind by the poller.py bug patched alongside this
script: `clean_extracted_text(snippet, title) or snippet` fell back to the
PRE-cleaning raw text whenever cleaning correctly determined there was
nothing but a repeat of the headline — most visibly on Google-News-proxied
RSS sources, whose <description> is just
`<a href="https://news.google.com/...">headline</a>&nbsp;&nbsp;<font>Source
</font>`, one line that normalizes to the same tokens as the article's own
title. Cleaning drops that line and correctly returns None; the old fallback
then resurrected the untouched HTML, whose literal "news.google.com"
substring was the source of "Google" wrongly showing as an entity on
unrelated stories (see enrichment.py's KNOWN_ENTITIES lookup). ~21.7k rows
confirmed affected in production, back to 2026-08-21.

Existing rows don't need a network fetch — just re-apply the current
(fixed) cleaning rule to the snippet already in hand: `clean_extracted_text
(snippet, title) or ""`, matching poller.py's corrected fallback exactly.

Safe to re-run any time (idempotent: rows already clean are left
untouched). Read-only by default — pass --apply to write.

Usage:
    python3 scripts/backfill_clean_snippets.py            # dry run
    python3 scripts/backfill_clean_snippets.py --apply    # writes changes
"""
import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select, update

from app.database import AsyncSessionLocal
from app.models import Article
from app.services.content_cleaner import clean_extracted_text

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main(apply: bool):
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Article.id, Article.title, Article.snippet).where(Article.snippet.isnot(None))
        )
        rows = result.all()
        logger.info(f"Found {len(rows)} articles with a stored snippet to re-clean.")

        changed = 0
        sample_shown = 0
        for row in rows:
            new_snippet = clean_extracted_text(row.snippet, row.title) or ""
            if new_snippet != row.snippet:
                changed += 1
                if sample_shown < 5:
                    logger.info(
                        f"  #{row.id} before={row.snippet[:80]!r} after={new_snippet[:80]!r}"
                    )
                    sample_shown += 1
                if apply:
                    await session.execute(
                        update(Article).where(Article.id == row.id).values(snippet=new_snippet)
                    )

        if apply:
            await session.commit()
            logger.info(f"Done. Updated {changed} of {len(rows)} rows ({len(rows) - changed} were already clean).")
        else:
            logger.info(
                f"DRY RUN — would update {changed} of {len(rows)} rows "
                f"({len(rows) - changed} already clean). Re-run with --apply to write."
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Write changes (default: dry run)")
    args = parser.parse_args()
    asyncio.run(main(apply=args.apply))
