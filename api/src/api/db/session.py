from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.config import settings


def engine_kwargs(database_url: str) -> dict[str, Any]:
    """Connection-pool settings for the async engine.

    SQLite (used in unit tests) uses a pool that rejects queue-pool sizing
    arguments, so only the real Postgres pool is tuned. ``pool_pre_ping`` is what
    makes Postgres failover transparent: a connection to a primary that has since
    been demoted is detected and discarded on checkout instead of erroring mid
    request.
    """
    if database_url.startswith("sqlite"):
        return {}
    return {
        "pool_pre_ping": True,
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        "pool_recycle": settings.db_pool_recycle_s,
    }


engine = create_async_engine(settings.database_url, **engine_kwargs(settings.database_url))
async_session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine, expire_on_commit=False
)
