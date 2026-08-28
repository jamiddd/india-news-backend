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
