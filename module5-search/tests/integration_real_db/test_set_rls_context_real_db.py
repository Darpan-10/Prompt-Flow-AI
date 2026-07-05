"""
Module 5 – Real PostgreSQL Regression Test: set_rls_context()

ADDED RETROACTIVELY after a critical bug was found while building Module
6 (which shares this exact RLS context pattern): `SET LOCAL app.X =
:param` raises a PostgreSQL syntax error when the value is a SQLAlchemy
bind parameter, and `SET LOCAL app.current_role = ...` separately fails
because `current_role` is a SQL-reserved keyword. Neither bug was caught
by the existing tests/unit and tests/integration suites because those
mock the database session entirely.

This test requires a real PostgreSQL instance.

Run with:
    createdb module5_rls_test
    psql module5_rls_test -c 'CREATE EXTENSION IF NOT EXISTS "uuid-ossp";'
    TEST_DATABASE_URL=postgresql+asyncpg://<user>:<pass>@localhost/module5_rls_test \\
        pytest tests/integration_real_db/test_set_rls_context_real_db.py -v
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.repositories.paper_repository import set_rls_context

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set -- this test requires a real PostgreSQL instance",
)


@pytest.fixture
async def engine():
    eng = create_async_engine(TEST_DATABASE_URL)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


class TestSetRlsContextAgainstRealPostgres:
    async def test_does_not_raise_for_any_role(self, session_factory):
        for role in ["faculty", "coordinator", "hod", "admin", "system_worker"]:
            async with session_factory() as session:
                async with session.begin():
                    await set_rls_context(session, "CSE", role, "test-user")

    async def test_values_actually_land_correctly(self, session_factory):
        async with session_factory() as session:
            async with session.begin():
                await set_rls_context(session, "ECE", "hod", "user-42")
                result = await session.execute(
                    text(
                        "SELECT current_setting('app.current_department', true), "
                        "current_setting('app.current_role', true), "
                        "current_setting('app.current_user_id', true)"
                    )
                )
                dept, role, uid = result.fetchone()
                assert dept == "ECE"
                assert role == "hod"
                assert uid == "user-42"

    async def test_repeated_calls_in_same_transaction_work(self, session_factory):
        """
        Mirrors hybrid search, which internally calls search_keyword()
        and search_semantic() -- each independently calls
        set_rls_context() again before its own query, all within the
        same overall request/transaction. Confirms calling this
        function multiple times in one transaction is safe (set_config's
        is_local=true semantics don't accumulate/conflict on repeat
        calls with the same or different values).
        """
        async with session_factory() as session:
            async with session.begin():
                await set_rls_context(session, "CSE", "admin", "user-1")
                await set_rls_context(session, "CSE", "admin", "user-1")
                await set_rls_context(session, "CSE", "admin", "user-1")
                result = await session.execute(
                    text("SELECT current_setting('app.current_department', true)")
                )
                assert result.scalar() == "CSE"
