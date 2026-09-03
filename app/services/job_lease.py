"""Row-based leases for singleton background jobs.

Replaces session-scoped pg_advisory_lock, which stopped providing mutual
exclusion when DATABASE_URL moved to a transaction-mode pooler on
2026-09-03. A transaction-mode pooler returns the backend to the pool at
every commit, so a lock acquired in one transaction is simply not held in
the next. Verified in production: a second client acquired a lock another
client believed it was holding. Any job whose critical section spans more
than one transaction needs something that does not live in a session.

A lease is a row (app/models.JobLease), so it does not care how connections
are pooled. Acquisition is one statement:

    INSERT ... ON CONFLICT (job_name) DO UPDATE ... WHERE expires_at < now()

which Postgres evaluates atomically — exactly one concurrent caller gets a
RETURNING row, and a lease is stealable only once it has actually expired.

TRADE-OFF, and why the heartbeat exists. A session lock was released by
Postgres the instant the holding connection dropped, so a crashed job never
left a stale lock. A row does not disappear on crash. A TTL alone forces a
choice between a long TTL (a crash blocks the job for that long) and a short
one (a slow-but-healthy run loses its lease and a second run starts —
exactly the thing being prevented). The heartbeat removes the choice: the
TTL stays short while a live holder keeps extending it, so a crashed holder
blocks for about one TTL rather than for the job's full duration.
"""
import asyncio
import contextlib
import logging
import uuid
from datetime import timedelta

from sqlalchemy import text

from app.database import AsyncSessionLocal

logger = logging.getLogger(__name__)

# TTL comfortably exceeds the heartbeat so a single slow tick cannot expire a
# live lease, while staying far below the poller's 20-minute cadence so a
# crashed run recovers well before the next one is due.
DEFAULT_TTL_SECONDS = 180
DEFAULT_HEARTBEAT_SECONDS = 45

_ACQUIRE = text("""
    INSERT INTO job_lease (job_name, owner, acquired_at, expires_at)
    VALUES (:job, :owner, now(), now() + make_interval(secs => :ttl))
    ON CONFLICT (job_name) DO UPDATE
       SET owner = EXCLUDED.owner,
           acquired_at = EXCLUDED.acquired_at,
           expires_at = EXCLUDED.expires_at
     WHERE job_lease.expires_at < now()
 RETURNING owner
""")

# Owner-scoped: if we somehow expired and another run took over, this
# extends nothing rather than stealing the lease back mid-flight.
_RENEW = text("""
    UPDATE job_lease
       SET expires_at = now() + make_interval(secs => :ttl)
     WHERE job_name = :job AND owner = :owner
""")

_RELEASE = text("DELETE FROM job_lease WHERE job_name = :job AND owner = :owner")


async def _heartbeat(job: str, owner: str, ttl: int, interval: int) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            # Its own short-lived session: the job owns the session passed to
            # it, and interleaving statements on that would corrupt whatever
            # transaction the job has open.
            async with AsyncSessionLocal() as s:
                result = await s.execute(_RENEW, {"job": job, "owner": owner, "ttl": ttl})
                await s.commit()
            if result.rowcount == 0:
                # We lost the lease — expired and taken over. Say so loudly;
                # it means the TTL is too short for this job's real runtime.
                logger.warning(
                    f"[job_lease] Lost lease {job!r} (owner={owner}) — another "
                    f"run has taken it over. TTL of {ttl}s is too short."
                )
                return
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # A failed renewal is survivable; the lease still has TTL left and
            # the next tick may succeed.
            logger.warning(f"[job_lease] Heartbeat failed for {job!r}: {e}")


@contextlib.asynccontextmanager
async def job_lease(
    job: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    heartbeat_seconds: int = DEFAULT_HEARTBEAT_SECONDS,
):
    """Yield True if this caller holds the lease, False if another run does.

    Yields rather than raising so callers keep the existing "log and skip"
    shape they had with pg_try_advisory_lock.
    """
    owner = uuid.uuid4().hex
    async with AsyncSessionLocal() as s:
        acquired = (
            await s.execute(_ACQUIRE, {"job": job, "owner": owner, "ttl": ttl_seconds})
        ).scalar_one_or_none()
        await s.commit()

    if acquired is None:
        yield False
        return

    beat = asyncio.create_task(_heartbeat(job, owner, ttl_seconds, heartbeat_seconds))
    try:
        yield True
    finally:
        beat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await beat
        # Release on its own session too: the caller's session may be in an
        # aborted transaction by now, which would reject the DELETE. This is
        # the same failure the old code guarded with a defensive rollback.
        try:
            async with AsyncSessionLocal() as s:
                await s.execute(_RELEASE, {"job": job, "owner": owner})
                await s.commit()
        except Exception as e:
            # Not fatal: the lease expires on its own within the TTL.
            logger.warning(f"[job_lease] Release failed for {job!r}: {e}")
