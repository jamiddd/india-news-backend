"""
One-off migration: adds `rss_only` to `sources` and sets it for publishers
whose article pages can't be scraped at all.

The whole NDTV group sits behind Akamai Bot Manager and 403s every article
fetch — verified from the droplet and from an unrelated network, so it isn't
our IP, and its own error page names the Akamai edge. curl_cffi's Chrome TLS
impersonation already fails to get past it. Measured before this change: NDTV
(3), NDTV Sports (20), NDTV Movies (24) and Gadgets 360 (27) each had 0 of 50
articles with scraped content, against 50 of 50 for Hindustan Times and Times
of India.

Gadgets 360 is worth noting separately: extractor.IMPERSONATE exists partly
because that site used to 403 a plain request and impersonation fixed it.
It now 403s through impersonation too, so that justification no longer holds
for this source even though it still does for others.

Marking it rss_only stops the poller spending a doomed request per article.
Such a source still ingests from its feed — title, snippet, RSS image — it
just never gets scraped body text, an og:image fallback, or video.

Safe to run multiple times.

Usage:
    python3 scripts/add_rss_only_column.py
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

# Matched on feed/homepage URL rather than a hardcoded id, so this behaves the
# same on any deployment where the ids differ.
# Gadgets 360 is NDTV-owned but on its own domain, so it needs listing
# separately. Feed URLs are all feedburner and carry no publisher domain,
# which is why homepage_url is matched too.
RSS_ONLY_DOMAINS = ["%ndtv.com%", "%gadgets360.com%"]


async def main():
    async with engine.begin() as conn:
        await conn.execute(text(
            "ALTER TABLE sources ADD COLUMN IF NOT EXISTS rss_only BOOLEAN NOT NULL DEFAULT FALSE"
        ))
        logger.info("sources.rss_only column is present.")

        for pattern in RSS_ONLY_DOMAINS:
            result = await conn.execute(
                text(
                    "UPDATE sources SET rss_only = TRUE "
                    "WHERE rss_only = FALSE AND (feed_url LIKE :pattern OR homepage_url LIKE :pattern)"
                ),
                {"pattern": pattern},
            )
            logger.info(f"Marked {result.rowcount} source(s) rss_only for {pattern}.")

        rows = (await conn.execute(
            text("SELECT id, name FROM sources WHERE rss_only = TRUE ORDER BY id")
        )).all()
        logger.info("RSS-only sources: " + (", ".join(f"{r.name} ({r.id})" for r in rows) or "none"))


if __name__ == "__main__":
    asyncio.run(main())
