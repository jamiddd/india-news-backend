from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from app.config import settings

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
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT,
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
