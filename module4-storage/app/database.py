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

    CRITICAL FIX (found via real-PostgreSQL integration testing while
    building Module 6, which shares this exact RLS context pattern):
    uses set_config(name, value, true), NOT `SET LOCAL ... = :param`.
    Two real bugs, both verified empirically against PostgreSQL 16:

    1. `SET LOCAL app.current_department = :d` (a bound parameter via
       SQLAlchemy's text()) raises `PostgresSyntaxError: syntax error at
       or near "$1"`. PostgreSQL's SET command is a utility statement,
       not regular DML, and does not accept protocol-level bind
       parameters for the value being set -- only a literal or a
       function call. This means EVERY call to this function would have
       failed the moment it ran against a real asyncpg connection,
       despite looking correct and despite working fine if you typed the
       same SQL with literal values directly into `psql`.

    2. `SET LOCAL app.current_role = ...` (literal or not) separately
       raises a DIFFERENT syntax error: `current_role` is a SQL-reserved
       keyword (synonym for CURRENT_USER) that PostgreSQL's grammar
       special-cases even as the second component of a dotted custom GUC
       name.

    set_config() fixes both at once: it's a regular function call (bind
    parameters work normally), and the variable name is passed as a
    STRING argument rather than bare SQL syntax (so the reserved-keyword
    restriction doesn't apply to a function argument). The third
    argument `true` means "is_local", equivalent to SET LOCAL's
    transaction-scoping -- RLS POLICY definitions reading via
    current_setting('app.current_role', true) need NO changes, since
    that was already a function call and unaffected by either bug.
    """
    tid = trace_id or str(uuid.uuid4())
    uid = user_id or "system"

    await session.execute(text("SELECT set_config('app.current_department', :d, true)"), {"d": department_code})
    await session.execute(text("SELECT set_config('app.current_role', :r, true)"), {"r": role})
    await session.execute(text("SELECT set_config('app.current_user_id', :u, true)"), {"u": uid})
    await session.execute(text("SELECT set_config('app.current_actor_type', :a, true)"), {"a": actor_type})
    await session.execute(text("SELECT set_config('app.change_reason', :c, true)"), {"c": change_reason})
    await session.execute(text("SELECT set_config('app.trace_id', :t, true)"), {"t": tid})


async def set_admin_context(session: AsyncSession) -> None:
    """Bypass RLS – admin only. See set_rls_context() docstring for why
    set_config() is used instead of SET LOCAL."""
    await session.execute(text("SELECT set_config('app.current_role', 'admin', true)"))
    await session.execute(text("SELECT set_config('app.current_department', '__admin__', true)"))
    await session.execute(text("SELECT set_config('app.current_user_id', 'system', true)"))
    await session.execute(text("SELECT set_config('app.current_actor_type', 'system', true)"))
    await session.execute(text("SELECT set_config('app.change_reason', 'admin_operation', true)"))
    await session.execute(text("SELECT set_config('app.trace_id', :t, true)"), {"t": str(uuid.uuid4())})


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
