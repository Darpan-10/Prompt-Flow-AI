"""
Module 6 – Integration Tests: Compliance Gate (real PostgreSQL required)

Tests ReportService._check_compliance_gate() against real data, proving
the exact SQL queries from the locked spec behave correctly: unresolved
error-severity validation_issues block generation, resolved ones don't,
warning-severity issues don't, and papers still PENDING_REVIEW/DRAFT
block generation while REJECTED papers don't.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import set_rls_context
from app.services.report_service import ComplianceGateError, ReportService

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


@pytest.fixture
def service(session_factory):
    # No S3 service needed -- only testing _check_compliance_gate, which
    # never touches S3.
    return ReportService(session_factory=session_factory, s3_service=object())  # type: ignore[arg-type]


async def _insert_paper(session, department_code: str, status: str = "PUBLISHED", year: int = 2024, **overrides):
    defaults = {
        "title": "Test Paper", "year": year, "paper_type": "journal",
        "faculty_id": str(uuid.uuid4()), "faculty_email": "test@srmap.edu.in",
        "department_code": department_code, "status": status, "overall_confidence": 0.9,
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


async def _insert_validation_issue(session, paper_id, department_code: str, severity: str, resolved: bool = False):
    from datetime import datetime, timezone
    await set_rls_context(session, department_code, "admin", "test-seed-user")
    await session.execute(
        text("""
            INSERT INTO validation_issues (paper_id, severity, resolved_at)
            VALUES (:paper_id, :severity, :resolved_at)
        """),
        {
            "paper_id": paper_id,
            "severity": severity,
            # asyncpg requires a native datetime object for a TIMESTAMPTZ
            # column, not an ISO-format string -- unlike psycopg2, it
            # does not implicitly parse string literals for typed
            # parameters.
            "resolved_at": datetime(2026, 1, 1, tzinfo=timezone.utc) if resolved else None,
        },
    )


class TestComplianceGatePassesCleanData:
    async def test_gate_passes_when_no_issues_no_pending(self, service, session_factory):
        async with session_factory() as session:
            await _insert_paper(session, "CSE", status="PUBLISHED")
            await session.commit()

        async with session_factory() as session:
            await set_rls_context(session, "CSE", "coordinator", "user-1")
            # Should NOT raise
            await service._check_compliance_gate(session, "CSE", 2023, 2024)


class TestComplianceGateBlocksOnUnresolvedErrors:
    async def test_gate_blocks_on_unresolved_error_severity_issue(self, service, session_factory):
        async with session_factory() as session:
            paper_id = await _insert_paper(session, "CSE", status="PUBLISHED")
            await _insert_validation_issue(session, paper_id, "CSE", severity="error", resolved=False)
            await session.commit()

        async with session_factory() as session:
            await set_rls_context(session, "CSE", "coordinator", "user-1")
            with pytest.raises(ComplianceGateError) as exc_info:
                await service._check_compliance_gate(session, "CSE", 2023, 2024)
            assert exc_info.value.unresolved_error_count == 1

    async def test_gate_passes_when_error_issue_is_resolved(self, service, session_factory):
        async with session_factory() as session:
            paper_id = await _insert_paper(session, "CSE", status="PUBLISHED")
            await _insert_validation_issue(session, paper_id, "CSE", severity="error", resolved=True)
            await session.commit()

        async with session_factory() as session:
            await set_rls_context(session, "CSE", "coordinator", "user-1")
            # Should NOT raise -- resolved_at IS NOT NULL excludes it
            await service._check_compliance_gate(session, "CSE", 2023, 2024)

    async def test_gate_passes_with_unresolved_warning_severity_issue(self, service, session_factory):
        """Only 'error' severity blocks generation -- 'warning' does not."""
        async with session_factory() as session:
            paper_id = await _insert_paper(session, "CSE", status="PUBLISHED")
            await _insert_validation_issue(session, paper_id, "CSE", severity="warning", resolved=False)
            await session.commit()

        async with session_factory() as session:
            await set_rls_context(session, "CSE", "coordinator", "user-1")
            await service._check_compliance_gate(session, "CSE", 2023, 2024)


class TestComplianceGateBlocksOnPendingPapers:
    async def test_gate_blocks_on_pending_review_paper(self, service, session_factory):
        async with session_factory() as session:
            await _insert_paper(session, "CSE", status="PENDING_REVIEW")
            await session.commit()

        async with session_factory() as session:
            await set_rls_context(session, "CSE", "coordinator", "user-1")
            with pytest.raises(ComplianceGateError) as exc_info:
                await service._check_compliance_gate(session, "CSE", 2023, 2024)
            assert exc_info.value.pending_paper_count == 1

    async def test_gate_blocks_on_draft_paper(self, service, session_factory):
        async with session_factory() as session:
            await _insert_paper(session, "CSE", status="DRAFT")
            await session.commit()

        async with session_factory() as session:
            await set_rls_context(session, "CSE", "coordinator", "user-1")
            with pytest.raises(ComplianceGateError):
                await service._check_compliance_gate(session, "CSE", 2023, 2024)

    async def test_gate_passes_with_rejected_paper(self, service, session_factory):
        """REJECTED is a final, deliberate outcome -- not 'unresolved'.
        Gating on it would make the compliance gate impossible to pass
        for any department with normal ingestion noise."""
        async with session_factory() as session:
            await _insert_paper(session, "CSE", status="REJECTED")
            await session.commit()

        async with session_factory() as session:
            await set_rls_context(session, "CSE", "coordinator", "user-1")
            await service._check_compliance_gate(session, "CSE", 2023, 2024)


class TestComplianceGateYearScoping:
    async def test_gate_ignores_issues_outside_year_range(self, service, session_factory):
        """An unresolved error on a 2020 paper must NOT block a
        2023-2024 academic year report."""
        async with session_factory() as session:
            paper_id = await _insert_paper(session, "CSE", status="PUBLISHED", year=2020)
            await _insert_validation_issue(session, paper_id, "CSE", severity="error", resolved=False)
            await session.commit()

        async with session_factory() as session:
            await set_rls_context(session, "CSE", "coordinator", "user-1")
            await service._check_compliance_gate(session, "CSE", 2023, 2024)


class TestComplianceGateDepartmentScoping:
    async def test_gate_ignores_other_departments_unresolved_errors(self, service, session_factory):
        """An unresolved error in ECE must not block a CSE report."""
        async with session_factory() as session:
            paper_id = await _insert_paper(session, "ECE", status="PUBLISHED")
            await _insert_validation_issue(session, paper_id, "ECE", severity="error", resolved=False)
            await session.commit()

        async with session_factory() as session:
            await set_rls_context(session, "CSE", "coordinator", "user-1")
            await service._check_compliance_gate(session, "CSE", 2023, 2024)
