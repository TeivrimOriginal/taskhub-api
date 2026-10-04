"""Async engine, session factory and the FastAPI session dependency.

The engine is created once per process (connections are expensive) and disposed on
application shutdown. The session dependency yields a transaction that is rolled back
on any unhandled error, which is what makes the integration tests isolated.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.base import Base

logger = get_logger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _engine_kwargs(settings: Settings) -> dict[str, object]:
    """Pool tuning only makes sense for a server process, not for tests/CLI."""
    kwargs: dict[str, object] = {
        "echo": settings.db_echo,
        "future": True,
        # asyncpg: fail loudly instead of hanging when the DB is unreachable.
        "pool_pre_ping": True,
    }
    if settings.is_testing:
        return kwargs
    kwargs.update(
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout_seconds,
        pool_recycle=settings.db_pool_recycle_seconds,
    )
    return kwargs


def get_engine(settings: Settings | None = None) -> AsyncEngine:
    """Return the process-wide async engine, creating it on first use."""
    global _engine  # noqa: PLW0603
    if _engine is None:
        resolved = settings or get_settings()
        logger.info(
            "creating_database_engine",
            dsn=resolved.masked_async_dsn(),
            environment=resolved.environment.value,
        )
        _engine = create_async_engine(resolved.postgres_async_uri, **_engine_kwargs(resolved))
    return _engine


def get_session_factory(settings: Settings | None = None) -> async_sessionmaker[AsyncSession]:
    """Return the process-wide session factory."""
    global _session_factory  # noqa: PLW0603
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(settings),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: one session per request, committed by the service layer."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def session_scope(settings: Settings | None = None) -> AsyncGenerator[AsyncSession, None]:
    """Transactional scope for scripts, CLI commands and Celery tasks."""
    factory = get_session_factory(settings)
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    """Close pooled connections — called from the application lifespan."""
    global _engine, _session_factory  # noqa: PLW0603
    if _engine is not None:
        await _engine.dispose()
        logger.info("database_engine_disposed")
    _engine = None
    _session_factory = None


async def create_all() -> None:
    """Create every table directly.

    Used by the test-suite and `make reset-db` only; production schema changes go
    through Alembic so they are reviewable and reversible.
    """
    # Importing the models package registers them on `Base.metadata`.
    from app import models  # noqa: F401

    engine = get_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    logger.info("schema_created", tables=len(Base.metadata.tables))


async def drop_all() -> None:
    """Drop every table. Test-suite helper — destructive by design."""
    from app import models  # noqa: F401

    engine = get_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    logger.warning("schema_dropped")
