"""
Module 4 – Real PostgreSQL Regression Test: set_rls_context()

ADDED RETROACTIVELY after a critical bug was found while building Module
6 (which shares this exact RLS context pattern): `SET LOCAL app.X =
:param` raises a PostgreSQL syntax error when the value is passed as a
SQLAlchemy bind parameter (PostgreSQL's SET command is a utility
statement and does not accept protocol-level bind parameters for the
value), and `SET LOCAL app.current_role = ...` separately fails because
`current_role` is a SQL-reserved keyword. Neither bug was caught by the
existing tests/test_module4.py suite because none of those tests run
against a real PostgreSQL connection -- they validate schema/business
logic with the DB entirely mocked out.

This test requires a real PostgreSQL instance. It is intentionally kept
in a SEPARATE directory (tests/integration_real_db/) from the main
tests/test_module4.py suite so it can be skipped in environments without
a database available, while still being part of the repo and runnable
on demand.

Run with:
    createdb module4_rls_test
    psql module4_rls_test -c 'CREATE EXTENSION IF NOT EXISTS "uuid-ossp";'
    TEST_DATABASE_URL=postgresql+asyncpg://<user>:<pass>@localhost/module4_rls_test \\
        pytest tests/integration_real_db/test_set_rls_context_real_db.py -v
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import set_admin_context, set_rls_context

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
    """
    The core regression guard: set_rls_context() and set_admin_context()
    must not raise against a real PostgreSQL connection, for any role
    (including 'admin', which is exactly where the current_role
    reserved-keyword bug bit hardest).
    """

    async def test_set_rls_context_does_not_raise_for_any_role(self, session_factory):
        for role in ["faculty", "coordinator", "hod", "admin", "system", "system_worker"]:
            async with session_factory() as session:
                async with session.begin():
                    await set_rls_context(
                        session,
                        department_code="CSE",
                        role=role,
                        user_id="test-user",
                        actor_type="user",
                        change_reason="regression_test",
                    )

    async def test_set_admin_context_does_not_raise(self, session_factory):
        async with session_factory() as session:
            async with session.begin():
                await set_admin_context(session)

    async def test_context_values_actually_land_correctly(self, session_factory):
        """Not just 'doesn't raise' -- confirm the values set are
        actually readable back via current_setting(), proving
        set_config()'s bind parameters substituted correctly rather than
        e.g. silently writing the literal string ':role' instead of the
        intended value."""
        async with session_factory() as session:
            async with session.begin():
                await set_rls_context(
                    session, department_code="ECE", role="hod",
                    user_id="user-42", actor_type="user", change_reason="test",
                )
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

    async def test_change_reason_passed_via_repository_update_path(self, session_factory):
        """
        Mirrors app/repository/repository.py's PaperRepository.update(),
        which has its OWN standalone set_config() call for change_reason
        (a second call site with the identical bug, fixed alongside
        set_rls_context()). Confirms that pattern in isolation too.
        """
        async with session_factory() as session:
            async with session.begin():
                await session.execute(
                    text("SELECT set_config('app.change_reason', :r, true)"),
                    {"r": "manual_correction"},
                )
                result = await session.execute(
                    text("SELECT current_setting('app.change_reason', true)")
                )
                assert result.scalar() == "manual_correction"
