"""Phase 1 migration: create `cluster_tokens` and backfill it for live clusters.

`cluster_tokens` is the inverted index that clustering now uses to find
candidate clusters for a new article (see app/models.py::ClusterToken and
poller._find_candidate_clusters). Without it, every new article looks like the
start of a brand-new story, so this must run BEFORE deploying the new poller.

Backfill is deliberately limited to clusters inside the matching window
(app.services.poller.CLUSTER_MATCH_WINDOW, 48h) plus a margin: older clusters
are never candidates, so indexing all ~48k of them would cost a large write
for rows that can never be read. `--all` overrides that if you ever need the
full index (e.g. to widen the window later).

Safe to re-run: the table creation is IF NOT EXISTS and the inserts are
ON CONFLICT DO NOTHING.

Usage:
    python3 scripts/add_cluster_tokens_table.py
    python3 scripts/add_cluster_tokens_table.py --days 7
    python3 scripts/add_cluster_tokens_table.py --all
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database import engine, AsyncSessionLocal
from app.models import Article, ClusterToken
from app.services.dedup import title_tokens

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DEFAULT_BACKFILL_DAYS = 3
CHUNK = 2000


async def create_table() -> None:
    async with engine.begin() as conn:
        await conn.execute(text(
            """
            CREATE TABLE IF NOT EXISTS cluster_tokens (
                cluster_id INTEGER NOT NULL
                    REFERENCES story_clusters(id) ON DELETE CASCADE,
                token VARCHAR(64) NOT NULL,
                PRIMARY KEY (cluster_id, token)
            )
            """
        ))
        # The lookup is always "which clusters contain any of these tokens",
        # which the (cluster_id, token) primary key cannot serve.
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_cluster_tokens_token "
            "ON cluster_tokens (token)"
        ))
    logger.info("cluster_tokens table and index ready.")


async def backfill(days: int | None) -> None:
    async with AsyncSessionLocal() as session:
        query = select(Article.cluster_id, Article.title).where(
            Article.cluster_id.isnot(None)
        )
        if days is not None:
            query = query.where(
                text("articles.published_at >= now() - make_interval(days => :d)")
            ).params(d=days)

        rows = (await session.execute(query)).all()
        logger.info("Indexing %d articles...", len(rows))

        pending: list[dict] = []
        written = 0
        seen: set[tuple[int, str]] = set()

        for cluster_id, title in rows:
            for tok in title_tokens(title or ""):
                key = (cluster_id, tok)
                if key in seen:
                    continue
                seen.add(key)
                pending.append({"cluster_id": cluster_id, "token": tok})

            if len(pending) >= CHUNK:
                await session.execute(
                    pg_insert(ClusterToken).values(pending)
                    .on_conflict_do_nothing(index_elements=["cluster_id", "token"])
                )
                await session.commit()
                written += len(pending)
                pending.clear()
                logger.info("  ... %d token rows written", written)

        if pending:
            await session.execute(
                pg_insert(ClusterToken).values(pending)
                .on_conflict_do_nothing(index_elements=["cluster_id", "token"])
            )
            await session.commit()
            written += len(pending)

        total = (await session.execute(
            text("SELECT count(*) FROM cluster_tokens")
        )).scalar()
        logger.info("Done. %d rows written this run; %d in table.", written, total)


async def main() -> None:
    days: int | None = DEFAULT_BACKFILL_DAYS
    if "--all" in sys.argv:
        days = None
    else:
        for i, arg in enumerate(sys.argv):
            if arg == "--days" and i + 1 < len(sys.argv):
                days = int(sys.argv[i + 1])

    await create_table()
    await backfill(days)


if __name__ == "__main__":
    asyncio.run(main())
