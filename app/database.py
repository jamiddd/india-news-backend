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
    # Postgres and the app share a docker-compose network with no long idle
    # gaps, so the stale-connection case pre-ping exists for is unlikely here
    # — worth the tradeoff to drop it rather than carry a live crash path.
    pool_pre_ping=False
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
