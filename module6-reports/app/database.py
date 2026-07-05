"""
Module 6 – Database Connection
Read-WRITE connection to the SAME shared PostgreSQL instance as Module 4
(unlike Module 5, which is strictly read-only -- Module 6 writes to its
own generated_reports/report_checksums tables and appends to Module 4's
audit_log).

CRITICAL: Module 4's papers/validation_issues/paper_versions tables have
Row-Level Security enabled, and the RLS policies key off
current_setting('app.current_department', true) etc. with the
missing-ok flag -- meaning if this session context is NOT set before a
query, RLS does not error, it just SILENTLY RETURNS ZERO ROWS (unless
role='admin'). For the compliance gate in particular, this would be a
dangerous false negative: an unresolved-errors COUNT(*) query that
returns 0 because RLS filtered everything out looks IDENTICAL to a
COUNT(*) that returns 0 because there really are no errors. Every query
against Module 4's tables MUST call set_rls_context() first.
"""

from __future__ import annotations

import logging
from typing import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

log = logging.getLogger(__name__)

engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    pool_pre_ping=True,
    echo=settings.DB_ECHO,
)

AsyncSessionFactory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def set_rls_context(
    session: AsyncSession,
    department_code: str,
    role: str,
    user_id: str,
) -> None:
    """
    Set session-level context for RLS policies on Module 4's tables.
    Must be called BEFORE any SELECT against papers/validation_issues/
    paper_versions within the same transaction.

    CRITICAL -- uses set_config(), NOT `SET LOCAL ... = :param`. Two
    real bugs were found and fixed here during development, both
    verified empirically against a real PostgreSQL 16 instance, not
    just reasoned about:

    1. `SET LOCAL app.current_department = :dept` (a bound parameter
       passed via SQLAlchemy's text()) raises `PostgresSyntaxError:
       syntax error at or near "$1"`. PostgreSQL's SET command is a
       utility statement, not a regular DML statement, and does not
       accept protocol-level bind parameters (asyncpg sends :dept as a
       $1 placeholder) for the VALUE being set -- it requires either a
       literal constant or a function call. This would have broken
       EVERY RLS-gated query in this module (and the same pattern was
       used in Module 4/5) the first time it ran against a real
       asyncpg connection, despite appearing to work fine when tested
       manually via `psql` with literal values typed directly in SQL.

    2. `SET LOCAL app.current_role = 'x'` (even with a literal, no bind
       parameter involved) raises a DIFFERENT syntax error:
       `current_role` is a SQL-reserved keyword (synonym for
       CURRENT_USER) that PostgreSQL's grammar special-cases even as
       the second component of a dotted custom GUC name.

    set_config('app.current_department', value, true) fixes BOTH
    problems at once: it's a regular function call (so bind parameters
    work normally), and the variable name is passed as a STRING
    argument rather than bare SQL syntax (so the reserved-keyword
    restriction on bare `SET ... current_role` simply doesn't apply --
    `current_role` is fine as a string). The third argument `true`
    means "is_local", equivalent to SET LOCAL's transaction-scoping.

    This means app.current_role (NOT a renamed app.current_user_role)
    is correct and matches what Module 4's RLS POLICY definitions
    already read via current_setting('app.current_role', true) -- no
    changes needed on the POLICY/read side, only here on the write side.
    """
    await session.execute(text("SELECT set_config('app.current_department', :dept, true)"), {"dept": department_code})
    await session.execute(text("SELECT set_config('app.current_role', :role, true)"), {"role": role})
    await session.execute(text("SELECT set_config('app.current_user_id', :uid, true)"), {"uid": user_id})
    # Module 6 writes new rows into the shared audit_log via the same
    # versioning/audit trigger infrastructure Module 4 set up -- that
    # trigger reads app.current_actor_type / app.current_user_id /
    # app.change_reason, so set those too for any INSERT/UPDATE this
    # session performs against Module 4-owned tables.
    await session.execute(text("SELECT set_config('app.current_actor_type', 'user', true)"))
    await session.execute(text("SELECT set_config('app.change_reason', 'report_generation', true)"))


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yields a database session."""
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def check_db_health() -> bool:
    try:
        async with AsyncSessionFactory() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        log.error("Database health check failed: %s", exc)
        return False


async def dispose_engine() -> None:
    await engine.dispose()
