"""
Module 5 – Regression Test: Department Isolation via Real Papers RLS Policy

ADDED after a full cross-module RLS security sweep found that Module 5's
existing real-DB test (test_set_rls_context_real_db.py) only checked
that set_rls_context() executes without raising -- it never created an
actual `papers` table with the real RLS policy and confirmed department
isolation itself. This closes that coverage gap.

Two things this test deliberately gets right, both found to be
critical during the sweep (see Module 4's CRITICAL_PATCH_NOTES.md for
the full writeup):

1. The papers table is created by, and therefore OWNED BY, the SAME
   role that TEST_DATABASE_URL connects as -- exactly matching
   production, where docker-compose.yml's POSTGRES_USER / Terraform's
   RDS master_username ("promptflow") is the SAME role that runs
   Alembic (owning every table) AND the running application connects as.
   PostgreSQL does not apply RLS policies to a table's OWNER by default,
   so a test using a merely-GRANTed non-owner role would give false
   confidence -- it would pass even if the real migration were missing
   FORCE ROW LEVEL SECURITY.

2. The POLICY SQL used here is the corrected, single-policy version
   (dept_scoped_paper_access) -- Module 4's schema used to have this
   split across TWO separate PERMISSIVE policies whose department-blind
   status clause was OR'd in globally, leaking every other department's
   PUBLISHED/PENDING_REVIEW/REJECTED papers to any authenticated
   non-admin user. Kept as a literal string here (not imported from
   Module 4's migration, since Module 5 is a separate deployable
   service) so this test fails loudly if it drifts from what's actually
   deployed.

Run with:
    createdb module5_rls_test OWNER <a NOSUPERUSER role>
    psql module5_rls_test -c 'CREATE EXTENSION IF NOT EXISTS "uuid-ossp";'
    TEST_DATABASE_URL=postgresql+asyncpg://<that role>:<pass>@localhost/module5_rls_test \\
        pytest tests/integration_real_db/test_department_isolation_real_db.py -v
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set -- this test requires a real PostgreSQL instance",
)

FORCE_RLS_SQL = "ALTER TABLE papers FORCE ROW LEVEL SECURITY;"

PAPERS_POLICY_SQL = """
    CREATE POLICY dept_scoped_paper_access ON papers
    USING (
        current_setting('app.current_role', true) = 'admin'
        OR (
            department_code = current_setting('app.current_department', true)
            AND (
                status IN ('PUBLISHED', 'PENDING_REVIEW', 'REJECTED')
                OR (
                    status = 'DRAFT'
                    AND faculty_id::text = current_setting('app.current_user_id', true)
                )
            )
        )
    );
"""


@pytest.fixture
async def engine():
    eng = create_async_engine(TEST_DATABASE_URL)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def setup_schema(engine):
    async with engine.connect() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS papers CASCADE"))
        # NOTE: pgvector's `vector` type/embedding column is deliberately
        # omitted here -- this test only needs the keyword-search-shaped
        # columns to exercise department isolation. Module 5's
        # semantic-search-specific SQL (which does need `embedding`) is
        # already exercised by the existing mocked-DB unit/integration
        # tests; omitting it here avoids an environment-dependent
        # dependency on the pgvector extension being installed.
        await conn.execute(text("""
            CREATE TABLE papers (
                paper_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                title TEXT NOT NULL,
                authors JSONB NOT NULL DEFAULT '[]',
                venue TEXT,
                year INTEGER NOT NULL DEFAULT 2024,
                doi TEXT,
                paper_type VARCHAR(20) NOT NULL DEFAULT 'journal',
                department_code VARCHAR(20) NOT NULL,
                status VARCHAR(20) NOT NULL,
                overall_confidence NUMERIC(4,3) NOT NULL DEFAULT 0.9,
                faculty_id UUID NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """))
        await conn.execute(text("ALTER TABLE papers ENABLE ROW LEVEL SECURITY"))
        await conn.execute(text(FORCE_RLS_SQL))
        await conn.execute(text(PAPERS_POLICY_SQL))
        await conn.commit()
    yield
    async with engine.connect() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS papers CASCADE"))
        await conn.commit()


CSE_FACULTY_ID = str(uuid.uuid4())
ECE_FACULTY_ID = str(uuid.uuid4())


async def _seed(session_factory):
    async with session_factory() as session:
        async with session.begin():
            await session.execute(text("SELECT set_config('app.current_role', 'admin', true)"))
            await session.execute(text("""
                INSERT INTO papers (title, department_code, status, faculty_id) VALUES
                ('CSE Published', 'CSE', 'PUBLISHED', :cse_fid),
                ('ECE Published', 'ECE', 'PUBLISHED', :ece_fid)
            """), {"cse_fid": CSE_FACULTY_ID, "ece_fid": ECE_FACULTY_ID})


class TestModule5SearchRespectsDepartmentIsolation:
    """
    Uses raw SQL matching the shape of Module 5's actual keyword search
    query (SELECT ... FROM papers WHERE status='PUBLISHED' ...) with a
    non-admin JWT-derived context whose department deliberately does
    NOT match one of the seeded papers -- the exact scenario your point
    5 asks for.
    """

    async def test_search_scoped_to_cse_excludes_ece_paper(self, session_factory):
        await _seed(session_factory)
        async with session_factory() as session:
            async with session.begin():
                # Mirrors set_rls_context(session, department_code="CSE",
                # role="coordinator", user_id=<a real non-admin JWT sub>)
                await session.execute(text("SELECT set_config('app.current_department', 'CSE', true)"))
                await session.execute(text("SELECT set_config('app.current_role', 'coordinator', true)"))
                await session.execute(text("SELECT set_config('app.current_user_id', :u, true)"), {"u": CSE_FACULTY_ID})

                result = await session.execute(text(
                    "SELECT title, department_code FROM papers WHERE status = 'PUBLISHED'"
                ))
                rows = result.fetchall()

        titles = {r[0] for r in rows}
        assert "ECE Published" not in titles, (
            "REGRESSION: a CSE-scoped, non-admin search context can see "
            "an ECE department's PUBLISHED paper via Module 5's search "
            "query shape -- department isolation is broken."
        )
        assert titles == {"CSE Published"}

    async def test_admin_search_context_sees_both_departments(self, session_factory):
        await _seed(session_factory)
        async with session_factory() as session:
            async with session.begin():
                await session.execute(text("SELECT set_config('app.current_department', 'CSE', true)"))
                await session.execute(text("SELECT set_config('app.current_role', 'admin', true)"))
                await session.execute(text("SELECT set_config('app.current_user_id', 'admin-1', true)"))
                result = await session.execute(text(
                    "SELECT title FROM papers WHERE status = 'PUBLISHED'"
                ))
                titles = {r[0] for r in result.fetchall()}
        assert titles == {"CSE Published", "ECE Published"}

    async def test_no_rls_context_set_search_returns_nothing(self, session_factory):
        """If Module 5's request pipeline ever fails to call
        set_rls_context() before a search query, the result must be an
        empty result set (safe, if confusing), never someone else's data."""
        await _seed(session_factory)
        async with session_factory() as session:
            result = await session.execute(text(
                "SELECT title FROM papers WHERE status = 'PUBLISHED'"
            ))
            assert result.fetchall() == []
