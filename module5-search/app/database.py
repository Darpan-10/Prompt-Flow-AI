"""
Module 5 – Database Connection
Read-only async engine connecting to the SAME PostgreSQL instance as Module 4.

Module 5 NEVER writes to the papers table. All queries are SELECT-only.
A smaller connection pool is used since search traffic is typically
read-heavy but lower-volume than Module 4's ingestion writes.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

log = logging.getLogger(__name__)

# ── Async engine (read-only usage pattern, not enforced at DB level here --
# enforcement is via the postgres user's GRANT permissions in production;
# locally, the same `promptflow` user is used for simplicity) ──────────────

engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    pool_pre_ping=True,  # detect stale connections before using them
    echo=settings.DB_ECHO,
)

AsyncSessionFactory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yields a database session, always rolled back (read-only)."""
    async with AsyncSessionFactory() as session:
        try:
            yield session
        finally:
            # Module 5 never commits -- every transaction is read-only.
            # Explicit rollback ensures no accidental writes are persisted
            # and releases any row locks immediately.
            await session.rollback()


@asynccontextmanager
async def get_db_context() -> AsyncGenerator[AsyncSession, None]:
    """Context manager version for use outside FastAPI's DI (e.g. background tasks)."""
    async with AsyncSessionFactory() as session:
        try:
            yield session
        finally:
            await session.rollback()


async def check_db_health() -> bool:
    """Quick connectivity check for /health endpoint."""
    try:
        async with AsyncSessionFactory() as session:
            from sqlalchemy import text
            await session.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        log.error("Database health check failed: %s", exc)
        return False


async def dispose_engine() -> None:
    """Call on app shutdown to cleanly close all pooled connections."""
    await engine.dispose()
