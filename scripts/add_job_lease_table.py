"""
One-off migration: create the job_lease table.

Backs app/services/job_lease.py, which replaces the session-scoped
pg_advisory_lock used by poll_all_sources and send_notifications. Those locks
stopped providing mutual exclusion when DATABASE_URL moved to a
transaction-mode pooler on 2026-09-03 — the pooler returns the backend at
every commit, so a lock taken in one transaction is not held in the next.
Verified in production: a second client acquired a lock another client
believed it held.

Idempotent. Usage:
    python3 scripts/add_job_lease_table.py
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import text

from app.database import admin_engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main() -> None:
    engine = admin_engine()
    try:
        async with engine.begin() as conn:
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS job_lease (
                    job_name    VARCHAR(64) PRIMARY KEY,
                    owner       VARCHAR(64) NOT NULL,
                    acquired_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    expires_at  TIMESTAMPTZ NOT NULL
                )
            """))
            await conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_job_lease_expires_at "
                "ON job_lease (expires_at)"
            ))
            logger.info("job_lease table and index are present.")

            # Any lease left behind by an older build is stale by definition
            # at migration time; clearing avoids a first run blocking on a
            # row nobody owns.
            result = await conn.execute(text("DELETE FROM job_lease WHERE expires_at < now()"))
            logger.info(f"Cleared {result.rowcount} expired lease row(s).")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
