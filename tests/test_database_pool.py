"""Guards the connection-pool and transaction-pooler configuration in
app/database.py. No DB or network — these assert wiring, not behaviour.

Motivated by 2026-09-03, when the pool was unbounded against a 15-client
Supabase session pooler and a maintenance script could not connect at all
(EMAXCONNSESSION).
"""
import asyncpg
import pytest
from sqlalchemy.dialects.postgresql.asyncpg import AsyncAdapt_asyncpg_dbapi

from app.config import settings
from app.database import _PGBOUNCER_CONNECT_ARGS, engine


class TestPoolSizing:
    def test_pool_is_explicitly_bounded(self):
        # The bug was inheriting SQLAlchemy's defaults (5 + 10 = 15 per
        # engine) across 8 engines sharing a 15-client ceiling.
        assert engine.pool.size() == settings.DB_POOL_SIZE
        assert settings.DB_POOL_SIZE <= 5

    def test_overflow_defaults_to_a_hard_cap(self):
        # max_overflow > 0 would let the pool open connections beyond
        # pool_size that the pooler will refuse; 0 makes exhaustion queue
        # client-side instead.
        assert settings.DB_MAX_OVERFLOW == 0


class TestPgbouncerConnectArgs:
    """Transaction mode can hand each transaction a different backend, so
    server-side prepared statements must be disabled and uniquely named."""

    def test_prepared_statement_caching_is_disabled(self):
        assert _PGBOUNCER_CONNECT_ARGS["prepared_statement_cache_size"] == 0
        assert _PGBOUNCER_CONNECT_ARGS["statement_cache_size"] == 0

    def test_statement_names_are_unique(self):
        # asyncpg's sequential default names collide between clients sharing
        # a backend -> DuplicatePreparedStatementError, but only under
        # concurrency.
        fn = _PGBOUNCER_CONNECT_ARGS["prepared_statement_name_func"]
        assert len({fn() for _ in range(1000)}) == 1000

    def test_sqlalchemy_pops_prepared_statement_kwargs(self):
        """The two prepared_statement_* kwargs belong in connect_args, where
        SQLAlchemy's adapter pops them. If a future SQLAlchemy moved them,
        they would be forwarded to asyncpg.connect() and raise TypeError at
        first connection — in production, not here. This pins that contract.
        """
        captured = {}

        class _Stop(Exception):
            pass

        def fake_connect(*args, **kwargs):
            captured.update(kwargs)
            raise _Stop  # before await_only(), so no greenlet needed

        dbapi = AsyncAdapt_asyncpg_dbapi(asyncpg)
        with pytest.raises(_Stop):
            dbapi.connect(async_creator_fn=fake_connect,
                          **dict(_PGBOUNCER_CONNECT_ARGS))

        assert "prepared_statement_cache_size" not in captured
        assert "prepared_statement_name_func" not in captured
        # asyncpg's own kwarg must still reach it.
        assert captured["statement_cache_size"] == 0

    def test_asyncpg_accepts_statement_cache_size(self):
        import inspect
        assert "statement_cache_size" in inspect.signature(asyncpg.connect).parameters
