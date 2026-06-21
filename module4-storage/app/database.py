"""
Module 4 – Database
Async SQLAlchemy engine + session factory with RLS context injection.
"""

from contextlib import asynccontextmanager
from typing import AsyncGenerator
import uuid

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy import text, event
from sqlalchemy.pool import NullPool

from app.config import settings

# ── Engine ────────────────────────────────────────────────────────────────────

def _make_engine(url: str | None = None, poolclass=None) -> AsyncEngine:
    kwargs: dict = dict(
        echo=settings.DB_ECHO,
        pool_pre_ping=True,
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
        pool_timeout=settings.DB_POOL_TIMEOUT,
        json_serializer=lambda obj: __import__("orjson").dumps(obj).decode(),
        json_deserializer=lambda s: __import__("orjson").loads(s),
    )
    if poolclass is not None:
        # NullPool used for migrations / testing
        kwargs = {k: v for k, v in kwargs.items()
                  if k not in {"pool_size", "max_overflow", "pool_timeout"}}
        kwargs["poolclass"] = poolclass

    return create_async_engine(url or settings.DATABASE_URL, **kwargs)


engine: AsyncEngine = _make_engine()

AsyncSessionFactory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


# ── RLS context ───────────────────────────────────────────────────────────────

async def set_rls_context(
    session: AsyncSession,
    *,
    department_code: str,
    role: str = "faculty",
    user_id: str | None = None,
    actor_type: str = "system",
    change_reason: str = "system_write",
    trace_id: str | None = None,
) -> None:
    """
    Inject per-transaction RLS context variables.
    Must be called inside an open transaction before any data access.
    """
    tid = trace_id or str(uuid.uuid4())
    uid = user_id or "system"

    await session.execute(
    text(f"SET LOCAL app.current_department = '{department_code}'")
)
    await session.execute(text(f"SET LOCAL app.current_role = :r"),          {"r": role})
    await session.execute(text(f"SET LOCAL app.current_user_id = :u"),       {"u": uid})
    await session.execute(text(f"SET LOCAL app.current_actor_type = :a"),    {"a": actor_type})
    await session.execute(text(f"SET LOCAL app.change_reason = :c"),         {"c": change_reason})
    await session.execute(text(f"SET LOCAL app.trace_id = :t"),              {"t": tid})


async def set_admin_context(session: AsyncSession) -> None:
    """Bypass RLS – admin only."""
    await session.execute(text("SET LOCAL app.current_role = 'admin'"))
    await session.execute(text("SET LOCAL app.current_department = '__admin__'"))
    await session.execute(text("SET LOCAL app.current_user_id = 'system'"))
    await session.execute(text("SET LOCAL app.current_actor_type = 'system'"))
    await session.execute(text("SET LOCAL app.change_reason = 'admin_operation'"))
    await session.execute(text(f"SET LOCAL app.trace_id = '{uuid.uuid4()}'"))


# ── Session dependency ────────────────────────────────────────────────────────

@asynccontextmanager
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency."""
    async with get_db_session() as session:
        yield session


# ── Health probe ──────────────────────────────────────────────────────────────

async def check_db_health() -> bool:
    try:
        async with AsyncSessionFactory() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
