"""Semantics of the row-lease SQL in app/services/job_lease.py.

No DB: these assert the statements say what the design requires. The
behavioural proof (two concurrent callers, only one wins) runs against real
Postgres — a lease is only as good as Postgres's atomicity, which is not
something a mock can demonstrate.
"""
import re

from app.services.job_lease import (
    _ACQUIRE,
    _RELEASE,
    _RENEW,
    DEFAULT_HEARTBEAT_SECONDS,
    DEFAULT_TTL_SECONDS,
)


def _sql(stmt):
    return re.sub(r"\s+", " ", str(stmt)).strip().lower()


class TestAcquireSemantics:
    def test_steals_only_an_expired_lease(self):
        # Without the WHERE, ON CONFLICT DO UPDATE would let any caller take
        # a live lease straight from its holder.
        sql = _sql(_ACQUIRE)
        assert "on conflict" in sql
        assert "do update" in sql
        assert "where job_lease.expires_at < now()" in sql

    def test_reports_whether_this_caller_won(self):
        # No RETURNING row means somebody else holds a live lease. Without
        # this the caller cannot tell success from contention.
        assert "returning owner" in _sql(_ACQUIRE)


class TestOwnerScoping:
    """A run that expired and was taken over must not be able to renew or
    release the lease now belonging to someone else."""

    def test_renew_is_owner_scoped(self):
        assert "owner = :owner" in _sql(_RENEW)

    def test_release_is_owner_scoped(self):
        assert "owner = :owner" in _sql(_RELEASE)


class TestTimings:
    def test_heartbeat_is_well_inside_the_ttl(self):
        # A single slow or failed heartbeat must not expire a live lease;
        # several must fit within one TTL.
        assert DEFAULT_HEARTBEAT_SECONDS * 3 <= DEFAULT_TTL_SECONDS

    def test_ttl_is_shorter_than_the_poll_interval(self):
        # The poller runs every 20 minutes. A crashed holder has to expire
        # before the next run is due, or the lease turns one crash into a
        # skipped cycle as well.
        assert DEFAULT_TTL_SECONDS < 20 * 60
