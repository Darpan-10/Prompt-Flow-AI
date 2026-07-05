"""
Module 6 – Integration Tests: RLS Context (real PostgreSQL required)

These tests run against an ACTUAL PostgreSQL instance loaded with
tests/integration/fixture_schema.sql -- not mocks. They exist
specifically because of a real bug found during development: `SET LOCAL
app.current_role = ...` raises a PostgreSQL syntax error (current_role
is a SQL-reserved keyword, even as the second component of a dotted GUC
name), which would have made every RLS-protected query in this module
(and in Module 4/5, which used the same naming pattern) silently fail in
a way that's easy to miss -- the exception happens, gets caught
somewhere, and the query "just" returns zero rows, which looks
identical to a correct empty result.

Run with: DATABASE_URL=postgresql+asyncpg://promptflow_test_user:testpass@localhost/promptflow_test pytest tests/integration/test_rls_context.py -v

See SETUP.md for how to stand up the test database these tests need.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import set_rls_context

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://promptflow_test_user:testpass@localhost/promptflow_test",
)

pytestmark = pytest.mark.integration


@pytest.fixture
async def engine():
    eng = create_async_engine(TEST_DATABASE_URL)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def clean_tables(session_factory):
    """Truncate before AND after each test so tests don't interfere with
    each other regardless of run order."""
    async with session_factory() as session:
        await session.execute(text(
            "TRUNCATE papers, validation_issues, generated_reports, report_checksums, audit_log CASCADE"
        ))
        await session.commit()
    yield
    async with session_factory() as session:
        await session.execute(text(
            "TRUNCATE papers, validation_issues, generated_reports, report_checksums, audit_log CASCADE"
        ))
        await session.commit()


async def _insert_paper(session, department_code: str, status: str = "PUBLISHED", **overrides):
    """
    Insert a test paper row. Must set RLS context as 'admin' BEFORE the
    INSERT -- the dept_isolation_papers POLICY's USING clause applies to
    INSERT/UPDATE as well as SELECT (no separate WITH CHECK was defined,
    so Postgres reuses USING for both), meaning an INSERT with no RLS
    context set at all gets rejected with "new row violates row-level
    security policy" (current_setting(..., true) returns NULL when
    unset, and department_code = NULL is never true). Using role='admin'
    here means this helper works regardless of which department_code is
    passed in, without needing the caller to also pass a matching role.
    """
    defaults = {
        "title": "Test Paper",
        "year": 2024,
        "paper_type": "journal",
        "faculty_id": str(uuid.uuid4()),
        "faculty_email": "test@srmap.edu.in",
        "department_code": department_code,
        "status": status,
        "overall_confidence": 0.9,
    }
    defaults.update(overrides)
    await set_rls_context(session, department_code, "admin", "test-seed-user")
    result = await session.execute(
        text("""
            INSERT INTO papers (title, year, paper_type, faculty_id, faculty_email,
                                 department_code, status, overall_confidence)
            VALUES (:title, :year, :paper_type, :faculty_id, :faculty_email,
                    :department_code, :status, :overall_confidence)
            RETURNING paper_id
        """),
        defaults,
    )
    return result.scalar()


class TestSetRlsContextDoesNotRaise:
    """
    The most basic possible test: calling set_rls_context() must not
    raise a syntax error. This is the exact regression that was caught
    -- the OLD code (SET LOCAL app.current_role = ...) raised
    asyncpg.exceptions.PostgresSyntaxError here.
    """

    async def test_set_rls_context_succeeds_for_every_role(self, session_factory):
        for role in ["faculty", "coordinator", "hod", "admin", "system_worker"]:
            async with session_factory() as session:
                async with session.begin():
                    # Must not raise
                    await set_rls_context(session, "CSE", role, "test-user-id")


class TestRlsZeroRowsWithoutContext:
    """
    Confirms the dangerous failure mode this whole test file exists to
    guard against: querying papers WITHOUT calling set_rls_context()
    first returns ZERO rows, not an error -- silently. This is exactly
    why the compliance gate query (and every other query against
    Module 4's tables) MUST call set_rls_context() before running.
    """

    async def test_query_without_rls_context_returns_zero_rows(self, session_factory):
        async with session_factory() as session:
            await _insert_paper(session, "CSE")
            await session.commit()

        # Fresh session, RLS context NEVER set
        async with session_factory() as session:
            result = await session.execute(text("SELECT COUNT(*) FROM papers"))
            count = result.scalar()
            assert count == 0, (
                "This is the EXPECTED (if dangerous) behavior: RLS with "
                "current_setting(..., true) (missing-ok) silently filters "
                "everything out when no session context is set, rather "
                "than erroring. This is why every query MUST call "
                "set_rls_context() first."
            )


class TestRlsScopingWithContext:
    async def test_correct_department_sees_its_own_papers(self, session_factory):
        async with session_factory() as session:
            await _insert_paper(session, "CSE", title="CSE Paper")
            await session.commit()

        async with session_factory() as session:
            async with session.begin():
                await set_rls_context(session, "CSE", "coordinator", "user-1")
                result = await session.execute(text("SELECT title FROM papers"))
                titles = [row[0] for row in result.fetchall()]
                assert titles == ["CSE Paper"]

    async def test_wrong_department_sees_nothing(self, session_factory):
        async with session_factory() as session:
            await _insert_paper(session, "CSE", title="CSE Paper")
            await session.commit()

        async with session_factory() as session:
            async with session.begin():
                await set_rls_context(session, "ECE", "coordinator", "user-1")
                result = await session.execute(text("SELECT title FROM papers"))
                assert result.fetchall() == []

    async def test_admin_role_sees_all_departments(self, session_factory):
        async with session_factory() as session:
            await _insert_paper(session, "CSE", title="CSE Paper")
            await _insert_paper(session, "ECE", title="ECE Paper")
            await session.commit()

        async with session_factory() as session:
            async with session.begin():
                # admin's OWN department is set to CSE, but admin should
                # still see ECE's paper too -- this is the exact bypass
                # condition the RLS policy encodes, and the exact thing
                # that breaks if the SET/current_setting variable names
                # ever drift out of sync between the writer and the
                # POLICY definition.
                await set_rls_context(session, "CSE", "admin", "admin-user")
                result = await session.execute(text("SELECT title FROM papers ORDER BY title"))
                titles = [row[0] for row in result.fetchall()]
                assert titles == ["CSE Paper", "ECE Paper"]

    async def test_non_admin_role_does_not_get_admin_bypass(self, session_factory):
        """Sanity check the inverse of the admin test -- a coordinator
        (not admin) must NOT see other departments, confirming the
        bypass condition is role-specific, not accidentally always-true."""
        async with session_factory() as session:
            await _insert_paper(session, "CSE", title="CSE Paper")
            await _insert_paper(session, "ECE", title="ECE Paper")
            await session.commit()

        async with session_factory() as session:
            async with session.begin():
                await set_rls_context(session, "CSE", "coordinator", "user-1")
                result = await session.execute(text("SELECT title FROM papers ORDER BY title"))
                titles = [row[0] for row in result.fetchall()]
                assert titles == ["CSE Paper"]


class TestRlsAppliesAcrossJoinedTable:
    """validation_issues has its own RLS policy that joins back to papers
    -- confirm that join-based policy also respects the context."""

    async def test_validation_issues_scoped_via_papers_join(self, session_factory):
        async with session_factory() as session:
            paper_id = await _insert_paper(session, "CSE")
            await session.execute(
                text("""
                    INSERT INTO validation_issues (paper_id, severity, resolved_at)
                    VALUES (:paper_id, 'error', NULL)
                """),
                {"paper_id": paper_id},
            )
            await session.commit()

        async with session_factory() as session:
            async with session.begin():
                await set_rls_context(session, "ECE", "coordinator", "user-1")
                result = await session.execute(text("SELECT COUNT(*) FROM validation_issues"))
                assert result.scalar() == 0

        async with session_factory() as session:
            async with session.begin():
                await set_rls_context(session, "CSE", "coordinator", "user-1")
                result = await session.execute(text("SELECT COUNT(*) FROM validation_issues"))
                assert result.scalar() == 1
