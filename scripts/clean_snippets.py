"""
One-off backfill: re-clean Article.snippet for existing rows using
content_cleaner.clean_extracted_text(), the same publisher-boilerplate
stripper poller.py now applies at ingestion time (see the commit that added
this call in poller.py's Pass 1).

Existing rows were stored before that fix, so publishers whose RSS
<description> bakes a repeated sign-off/CTA block into every item (News18's
"CNN-News18 is your trusted source..." + social links, verbatim on every
article) still carry it in Article.snippet. Beyond being ugly in the app's
snippet previews, that shared boilerplate text is long and repetitive enough
to have caused real false-positive matches in shares_topic() (dedup.py) —
see scripts/repair_runaway_clusters.py, which should be re-run with --apply
after this script to catch any clusters that only look wrong once the
boilerplate is gone.

Read-only by default (--apply required to write).

Usage:
    python3 scripts/clean_snippets.py [--apply] [--batch-size 500]
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import Article
from app.services.content_cleaner import clean_extracted_text


async def main(apply: bool, batch_size: int):
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Article.id, Article.title, Article.snippet).where(Article.snippet.isnot(None))
        )
        rows = result.all()
        print(f"Scanning {len(rows)} articles with a snippet...")

        changed = 0
        for i, (article_id, title, snippet) in enumerate(rows):
            cleaned = clean_extracted_text(snippet, title) or snippet
            if cleaned != snippet:
                changed += 1
                if apply:
                    article = await session.get(Article, article_id)
                    article.snippet = cleaned

            if apply and (i + 1) % batch_size == 0:
                await session.commit()
                print(f"  committed {i + 1}/{len(rows)}...")

        if apply:
            await session.commit()
            print(f"Done: cleaned {changed} of {len(rows)} snippets.")
        else:
            print(f"Dry run: {changed} of {len(rows)} snippets would change. Re-run with --apply to write.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write changes (default: dry run / report only)")
    parser.add_argument("--batch-size", type=int, default=500, help="Commit every N updated rows")
    args = parser.parse_args()
    asyncio.run(main(apply=args.apply, batch_size=args.batch_size))
