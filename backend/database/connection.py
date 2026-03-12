"""
Async SQLAlchemy engine and session factory.
Supports SQLite (development) and PostgreSQL (production) via DATABASE_URL.
"""

from __future__ import annotations

from pathlib import Path

import structlog
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from backend.config import settings
from backend.database.models import Base

logger = structlog.get_logger(__name__)

_engine: AsyncEngine | None = None
_async_session_factory: async_sessionmaker[AsyncSession] | None = None


def _get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        is_sqlite = settings.DATABASE_URL.startswith("sqlite")

        # Ensure data directory exists for SQLite
        if is_sqlite:
            db_path = settings.DATABASE_URL.split("///")[-1]
            if db_path and db_path != ":memory:":
                Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        if is_sqlite:
            # SQLite: one shared connection, WAL mode for concurrent readers,
            # busy_timeout so writers wait instead of raising immediately.
            _engine = create_async_engine(
                settings.DATABASE_URL,
                echo=False,
                connect_args={
                    "check_same_thread": False,
                    "timeout": 30,          # wait up to 30 s for lock
                },
                poolclass=StaticPool,       # single shared connection → no lock contention
            )
        else:
            _engine = create_async_engine(
                settings.DATABASE_URL,
                echo=settings.DEBUG,
                pool_pre_ping=True,
                pool_size=10,
                max_overflow=20,
            )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _async_session_factory
    if _async_session_factory is None:
        _async_session_factory = async_sessionmaker(
            _get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _async_session_factory


async def get_db() -> AsyncSession:  # type: ignore[return]
    """FastAPI dependency that yields an async DB session."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """Create all tables if they do not yet exist (idempotent)."""
    engine = _get_engine()
    async with engine.begin() as conn:
        # Enable WAL mode for SQLite — allows concurrent reads during writes
        if settings.DATABASE_URL.startswith("sqlite"):
            await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
            await conn.exec_driver_sql("PRAGMA synchronous=NORMAL")
            await conn.exec_driver_sql("PRAGMA busy_timeout=30000")
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database initialised", url=settings.DATABASE_URL.split("///")[0])


async def close_db() -> None:
    """Dispose the async engine on application shutdown."""
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        logger.info("Database connection pool closed")
