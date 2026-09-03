import uuid

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from app.config import settings

# Transaction-mode pooling (Supabase port 6543 / pgbouncer) multiplexes many
# clients onto few backend connections and can hand each transaction a
# DIFFERENT backend. Server-side prepared statements are per-backend, so the
# defaults break in two distinct ways:
#
#   1. asyncpg prepares every statement and caches it by name. A cached
#      statement created on backend A is invalid when the next transaction
#      lands on backend B -> InvalidSQLStatementNameError.
#   2. asyncpg's default names are sequential (__asyncpg_stmt_1__, ...), so
#      two clients sharing a backend collide ->
#      DuplicatePreparedStatementError. This one only appears under
#      concurrency, which is exactly when you don't want to discover it.
#
# All three settings below are needed; the first two are consumed by
# SQLAlchemy's asyncpg adapter (it pops them from the connect kwargs), the
# third by asyncpg.connect itself. Session mode and direct connections do not
# need any of this — a dedicated backend per client makes prepared statements
# safe — so it is gated on DB_PGBOUNCER rather than always-on. Verified
# against SQLAlchemy 2.0.52: AsyncAdapt_asyncpg_dbapi.connect() pops
# prepared_statement_cache_size and prepared_statement_name_func, so they
# belong in connect_args; passing them to create_async_engine() as top-level
# kwargs forwards them to asyncpg.connect() and raises TypeError.
_PGBOUNCER_CONNECT_ARGS = {
    "prepared_statement_cache_size": 0,
    "prepared_statement_name_func": lambda: f"__asyncpg_{uuid.uuid4()}__",
    "statement_cache_size": 0,
}

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    future=True,
    # pool_pre_ping was on, but checking out a fresh connection after one gets
    # invalidated (e.g. following a failed transaction) hit a SQLAlchemy
    # async/asyncpg incompatibility: the pre-ping's do_ping() isn't properly
    # greenlet-bridged during that specific reconnect path, raising
    # MissingGreenlet instead of the query it was guarding. Observed live: a
    # poller rollback recovery immediately hit this on the very next source.
    # That reasoning assumed Postgres and the app shared a docker-compose
    # network with no long idle gaps — true when this was written, false
    # since 2026-08-22 (prod moved to a remote managed Postgres). pre_ping
    # stays off for the same MissingGreenlet reason, but pool_recycle now
    # does the job pre_ping would have: proactively drops and replaces any
    # connection older than this, rather than waiting to discover it's dead
    # mid-request. Same class of fix already applied to the Redis client
    # (see redis_client.py's health_check_interval/socket_keepalive) when
    # that migration first surfaced idle-connection drops, just never
    # carried over here.
    pool_pre_ping=False,
    pool_recycle=1800,
    # Connection budget. SQLAlchemy's defaults (pool_size=5, max_overflow=10)
    # let ONE engine demand 15 connections. Every process that imports this
    # module builds its own engine, and prod runs, per droplet:
    #
    #     2 uvicorn workers + crossword_scheduler + poll_scheduler = 4 engines
    #
    # across TWO droplets (newsapp, newsapp-2) sharing a single Supabase
    # session-mode pooler capped at 15 clients total. So the defaults asked
    # for up to 8 x 15 = 120 connections against a ceiling of 15 — ~8x
    # oversubscribed before a single request arrives. Observed 2026-09-03,
    # when a one-off maintenance script could not get a connection at all:
    #
    #     asyncpg.exceptions.InternalServerError: (EMAXCONNSESSION)
    #     max clients reached in session mode
    #
    # 8 engines x 2 = 16 steady-state worst case, and the two schedulers are
    # idle almost all the time, so this fits 15 in practice while leaving
    # room for admin scripts. max_overflow=0 makes the cap a real cap:
    # exhaustion queues here for pool_timeout seconds instead of opening
    # connections the server will refuse.
    #
    # These are env-tunable (DB_POOL_SIZE / DB_MAX_OVERFLOW / DB_POOL_TIMEOUT)
    # because the right numbers depend on droplet count, worker count, and
    # the pooler's ceiling — none of which this module can see. Raise them
    # only alongside a corresponding change to one of those.
    #
    # Against a TRANSACTION pooler these numbers can go up substantially —
    # that pooler's whole purpose is tolerating many short-lived clients, and
    # its client ceiling is far above session mode's 15. Raise DB_POOL_SIZE
    # once DB_PGBOUNCER is on and you have confirmed the new ceiling; the
    # defaults here stay sized for the stricter session-mode case so that
    # flipping one setting can never overcommit the other.
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    connect_args=_PGBOUNCER_CONNECT_ARGS if settings.DB_PGBOUNCER else {},
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False
)

Base = declarative_base()

async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
