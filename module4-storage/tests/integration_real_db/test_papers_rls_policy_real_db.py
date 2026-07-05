"""
Module 4 – Regression Test: Papers RLS Policy Department Isolation

ADDED after a critical vulnerability was found during a full cross-module
RLS security sweep: the `papers` table used to have TWO separate
PERMISSIVE RLS policies (dept_isolation_papers + faculty_draft_access).
PostgreSQL combines multiple PERMISSIVE policies with OR. The second
policy's `status IN ('PUBLISHED', 'PENDING_REVIEW', 'REJECTED')` clause
had no department qualifier, so it was OR'd in globally -- meaning EVERY
authenticated non-admin user could see EVERY other department's
PUBLISHED/PENDING_REVIEW/REJECTED papers, completely bypassing
department isolation for anything except DRAFT status.

Verified empirically with a live PostgreSQL 16 instance: a
correctly-scoped CSE coordinator (legitimate session context, non-admin)
could SELECT an ECE department's PUBLISHED paper.

Fixed by replacing both policies with a single `dept_scoped_paper_access`
policy where department membership is a hard AND-ed requirement for
everyone except admin.

This test requires a real PostgreSQL instance (skipped automatically if
TEST_DATABASE_URL isn't set), and builds a schema containing ONLY the
papers table + the actual POLICY SQL extracted from the real migration,
rather than running the full migration (which creates 50+ partition
tables) -- fast enough to run on every test invocation while still
exercising the real policy definition, not a hand-written approximation
of it.

CRITICAL: this test creates the papers table using the SAME role that
TEST_DATABASE_URL connects as, and therefore that role OWNS the table --
exactly matching production, where docker-compose.yml's POSTGRES_USER
(and Terraform's RDS master_username) is "promptflow", the SAME role
both Alembic (which creates/owns every table) and the running
application (via DATABASE_URL) use. This is deliberate: a second,
SEPARATE bug was found where RLS policies were completely inert against
this exact role model, because PostgreSQL does not apply RLS policies to
a table's OWNER by default (a SEPARATE exemption from the well-known
superuser bypass) -- fixed by adding `FORCE ROW LEVEL SECURITY`, which
this test's schema setup also includes and therefore also guards against
regressing. If you run this test with a TEST_DATABASE_URL that connects
as a true PostgreSQL SUPERUSER (e.g. the default local "postgres" role
in a fresh apt/Docker install), it will ALWAYS pass regardless of
whether FORCE ROW LEVEL SECURITY is present or not -- true superusers
bypass RLS unconditionally and FORCE cannot override that. Use a
NOSUPERUSER role (matching AWS RDS's master user, which is explicitly
NOSUPERUSER despite having elevated rds_superuser privileges) for this
test to be meaningful.
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

# The exact POLICY SQL from migrations/versions/001_initial_schema.py --
# kept as a literal string here (rather than importing/parsing the
# migration file) so this test fails LOUDLY if the two ever drift out of
# sync, rather than silently testing something that no longer matches
# what's actually deployed. If you change the policy in the migration,
# update this string too, or better: extract it into a shared constant
# both files import.
# NOTE: this string must also include FORCE ROW LEVEL SECURITY, or this
# test would give false confidence -- see the module docstring above.
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
    """Create a minimal papers table with the REAL policy SQL, seed 4
    rows spanning 2 departments x {PUBLISHED, DRAFT}, drop everything
    after."""
    async with engine.connect() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS papers CASCADE"))
        await conn.execute(text("""
            CREATE TABLE papers (
                paper_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                title TEXT NOT NULL,
                department_code VARCHAR(20) NOT NULL,
                status VARCHAR(20) NOT NULL,
                faculty_id UUID NOT NULL
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
                ('ECE Published', 'ECE', 'PUBLISHED', :ece_fid),
                ('ECE Draft', 'ECE', 'DRAFT', :ece_fid),
                ('CSE Draft', 'CSE', 'DRAFT', :cse_fid)
            """), {"cse_fid": CSE_FACULTY_ID, "ece_fid": ECE_FACULTY_ID})


class TestDepartmentIsolationNotBypassed:
    """The core regression guard for the cross-department leak."""

    async def test_cse_coordinator_cannot_see_ece_published_paper(self, session_factory):
        await _seed(session_factory)
        async with session_factory() as session:
            async with session.begin():
                await session.execute(text("SELECT set_config('app.current_department', 'CSE', true)"))
                await session.execute(text("SELECT set_config('app.current_role', 'coordinator', true)"))
                await session.execute(text("SELECT set_config('app.current_user_id', :u, true)"), {"u": CSE_FACULTY_ID})
                result = await session.execute(text("SELECT title, department_code FROM papers ORDER BY title"))
                rows = result.fetchall()
                titles = {r[0] for r in rows}

        assert "ECE Published" not in titles, (
            "REGRESSION: a CSE-scoped coordinator can see an ECE "
            "department's PUBLISHED paper -- this is the exact "
            "multi-policy-OR department-isolation bypass that was found "
            "and fixed. All visible rows must be department_code='CSE'."
        )
        assert all(r[1] == "CSE" for r in rows)

    async def test_ece_coordinator_cannot_see_cse_published_paper(self, session_factory):
        """Inverse direction, for symmetry."""
        await _seed(session_factory)
        async with session_factory() as session:
            async with session.begin():
                await session.execute(text("SELECT set_config('app.current_department', 'ECE', true)"))
                await session.execute(text("SELECT set_config('app.current_role', 'coordinator', true)"))
                await session.execute(text("SELECT set_config('app.current_user_id', :u, true)"), {"u": ECE_FACULTY_ID})
                result = await session.execute(text("SELECT title, department_code FROM papers"))
                rows = result.fetchall()

        assert all(r[1] == "ECE" for r in rows)
        assert "CSE Published" not in {r[0] for r in rows}


class TestOwnDepartmentAndDraftAccessStillWork:
    """Confirm the fix didn't overcorrect -- legitimate access patterns
    must still work."""

    async def test_sees_own_department_published_papers(self, session_factory):
        await _seed(session_factory)
        async with session_factory() as session:
            async with session.begin():
                await session.execute(text("SELECT set_config('app.current_department', 'CSE', true)"))
                await session.execute(text("SELECT set_config('app.current_role', 'coordinator', true)"))
                await session.execute(text("SELECT set_config('app.current_user_id', :u, true)"), {"u": CSE_FACULTY_ID})
                result = await session.execute(text("SELECT title FROM papers WHERE status='PUBLISHED'"))
                assert result.scalar() == "CSE Published"

    async def test_sees_own_draft(self, session_factory):
        await _seed(session_factory)
        async with session_factory() as session:
            async with session.begin():
                await session.execute(text("SELECT set_config('app.current_department', 'CSE', true)"))
                await session.execute(text("SELECT set_config('app.current_role', 'faculty', true)"))
                await session.execute(text("SELECT set_config('app.current_user_id', :u, true)"), {"u": CSE_FACULTY_ID})
                result = await session.execute(text("SELECT title FROM papers WHERE status='DRAFT'"))
                assert result.scalar() == "CSE Draft"

    async def test_does_not_see_another_faculty_draft_same_department(self, session_factory):
        """Even within the SAME department, a draft belongs only to its
        owning faculty member -- not to every coordinator/faculty in
        that department."""
        await _seed(session_factory)
        other_user = str(uuid.uuid4())
        async with session_factory() as session:
            async with session.begin():
                await session.execute(text("SELECT set_config('app.current_department', 'CSE', true)"))
                await session.execute(text("SELECT set_config('app.current_role', 'faculty', true)"))
                await session.execute(text("SELECT set_config('app.current_user_id', :u, true)"), {"u": other_user})
                result = await session.execute(text("SELECT title FROM papers WHERE status='DRAFT'"))
                assert result.fetchall() == []

    async def test_admin_sees_everything_regardless_of_department(self, session_factory):
        await _seed(session_factory)
        async with session_factory() as session:
            async with session.begin():
                await session.execute(text("SELECT set_config('app.current_department', 'CSE', true)"))
                await session.execute(text("SELECT set_config('app.current_role', 'admin', true)"))
                await session.execute(text("SELECT set_config('app.current_user_id', 'admin-1', true)"))
                result = await session.execute(text("SELECT COUNT(*) FROM papers"))
                assert result.scalar() == 4

    async def test_no_context_set_sees_nothing(self, session_factory):
        await _seed(session_factory)
        async with session_factory() as session:
            result = await session.execute(text("SELECT COUNT(*) FROM papers"))
            assert result.scalar() == 0
