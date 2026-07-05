"""
Module 6 – Integration Tests: Full Report Generation Pipeline
(real PostgreSQL required, S3 mocked)

Exercises ReportService.run_generation() end-to-end against real data:
compliance gate -> fetch -> render (REAL WeasyPrint/openpyxl, not
mocked) -> checksum -> "upload" (mocked S3) -> persist status/checksum/
audit_log. This is the closest thing to a true end-to-end test in this
module without needing actual AWS credentials.
"""

from __future__ import annotations

import os
import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import set_rls_context
from app.services.report_service import ReportService

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


@pytest.fixture
def mock_s3():
    mock = AsyncMock()
    mock.upload_file = AsyncMock(return_value="s3://promptflow-reports-dev/reports/CSE/2023-2024/criteria_iii_test.pdf")
    return mock


@pytest.fixture
def service(session_factory, mock_s3):
    return ReportService(session_factory=session_factory, s3_service=mock_s3)


async def _insert_paper(session, department_code: str, status: str = "PUBLISHED", **overrides):
    defaults = {
        "title": "Attention Is All You Need",
        "authors": [{"name": "Ashish Vaswani", "affiliation": "Google Brain"}],
        "year": 2024, "paper_type": "conference", "doi": "10.48550/arXiv.1706.03762",
        "faculty_id": str(uuid.uuid4()), "faculty_email": "rajesh.kumar@srmap.edu.in",
        "department_code": department_code, "status": status, "overall_confidence": 0.94,
    }
    defaults.update(overrides)
    await set_rls_context(session, department_code, "admin", "test-seed-user")
    import orjson
    result = await session.execute(
        text("""
            INSERT INTO papers (title, authors, year, paper_type, doi, faculty_id, faculty_email,
                                 department_code, status, overall_confidence)
            VALUES (:title, :authors, :year, :paper_type, :doi, :faculty_id, :faculty_email,
                    :department_code, :status, :overall_confidence)
            RETURNING paper_id
        """),
        {**defaults, "authors": orjson.dumps(defaults["authors"]).decode()},
    )
    return result.scalar()


async def _insert_report_request(session, report_type: str, department_code: str, academic_year: str, generated_by: str, faculty_id=None):
    await set_rls_context(session, department_code, "coordinator", generated_by)
    result = await session.execute(
        text("""
            INSERT INTO generated_reports (report_type, department_code, academic_year, faculty_id, generated_by)
            VALUES (:rt, :dept, :year, :fid, :by)
            RETURNING report_id
        """),
        {"rt": report_type, "dept": department_code, "year": academic_year, "fid": str(faculty_id) if faculty_id else None, "by": generated_by},
    )
    return result.scalar()


class TestFullGenerationPipelineNaacCriteriaIII:
    async def test_successful_generation_pdf(self, service, session_factory, mock_s3):
        async with session_factory() as session:
            await _insert_paper(session, "CSE")
            report_id = await _insert_report_request(session, "NAAC_CRITERIA_III", "CSE", "2023-2024", "coordinator-1")
            await session.commit()

        await service.run_generation(
            report_id=report_id, report_type="NAAC_CRITERIA_III",
            department_code="CSE", academic_year="2023-2024", output_format="pdf",
            generated_by="coordinator-1", role="coordinator",
        )

        # Verify final DB state
        async with session_factory() as session:
            await set_rls_context(session, "CSE", "admin", "verify")
            result = await session.execute(
                text("SELECT status, s3_uri, checksum_sha256, total_papers, error_message FROM generated_reports WHERE report_id = :id"),
                {"id": str(report_id)},
            )
            row = dict(result.fetchone()._mapping)

        assert row["status"] == "COMPLETED"
        assert row["s3_uri"] == "s3://promptflow-reports-dev/reports/CSE/2023-2024/criteria_iii_test.pdf"
        assert row["checksum_sha256"] is not None
        assert len(row["checksum_sha256"]) == 64  # real SHA-256 hex digest
        assert row["total_papers"] == 1
        assert row["error_message"] is None

        # S3 upload was actually called with real PDF bytes
        assert mock_s3.upload_file.called
        call_kwargs = mock_s3.upload_file.call_args.kwargs
        assert call_kwargs["content_type"] == "application/pdf"
        assert call_kwargs["file_bytes"][:5] == b"%PDF-"  # real WeasyPrint output, not a mock

    async def test_successful_generation_xlsx(self, service, session_factory, mock_s3):
        async with session_factory() as session:
            await _insert_paper(session, "CSE")
            report_id = await _insert_report_request(session, "NAAC_CRITERIA_III", "CSE", "2023-2024", "coordinator-1")
            await session.commit()

        await service.run_generation(
            report_id=report_id, report_type="NAAC_CRITERIA_III",
            department_code="CSE", academic_year="2023-2024", output_format="xlsx",
            generated_by="coordinator-1", role="coordinator",
        )

        call_kwargs = mock_s3.upload_file.call_args.kwargs
        assert call_kwargs["content_type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        assert call_kwargs["file_bytes"][:2] == b"PK"  # real openpyxl xlsx (zip) output

    async def test_checksum_row_inserted(self, service, session_factory):
        async with session_factory() as session:
            await _insert_paper(session, "CSE")
            report_id = await _insert_report_request(session, "NAAC_CRITERIA_III", "CSE", "2023-2024", "coordinator-1")
            await session.commit()

        await service.run_generation(
            report_id=report_id, report_type="NAAC_CRITERIA_III",
            department_code="CSE", academic_year="2023-2024", output_format="pdf",
            generated_by="coordinator-1", role="coordinator",
        )

        async with session_factory() as session:
            result = await session.execute(
                text("SELECT event_type, checksum_sha256 FROM report_checksums WHERE report_id = :id"),
                {"id": str(report_id)},
            )
            row = dict(result.fetchone()._mapping)
        assert row["event_type"] == "GENERATED"
        assert len(row["checksum_sha256"]) == 64

    async def test_audit_log_entry_written(self, service, session_factory):
        async with session_factory() as session:
            await _insert_paper(session, "CSE")
            report_id = await _insert_report_request(session, "NAAC_CRITERIA_III", "CSE", "2023-2024", "coordinator-1")
            await session.commit()

        await service.run_generation(
            report_id=report_id, report_type="NAAC_CRITERIA_III",
            department_code="CSE", academic_year="2023-2024", output_format="pdf",
            generated_by="coordinator-1", role="coordinator",
        )

        async with session_factory() as session:
            result = await session.execute(
                text("SELECT action, actor_id, resource_id FROM audit_log WHERE resource_id = :id"),
                {"id": str(report_id)},
            )
            row = dict(result.fetchone()._mapping)
        assert row["action"] == "report_generated"
        assert row["actor_id"] == "coordinator-1"


class TestFullGenerationPipelineComplianceGateBlocks:
    async def test_generation_fails_status_on_compliance_gate_block(self, service, session_factory, mock_s3):
        from datetime import datetime, timezone
        async with session_factory() as session:
            paper_id = await _insert_paper(session, "CSE")
            await set_rls_context(session, "CSE", "admin", "seed")
            await session.execute(
                text("INSERT INTO validation_issues (paper_id, severity, resolved_at) VALUES (:pid, 'error', NULL)"),
                {"pid": paper_id},
            )
            report_id = await _insert_report_request(session, "NAAC_CRITERIA_III", "CSE", "2023-2024", "coordinator-1")
            await session.commit()

        await service.run_generation(
            report_id=report_id, report_type="NAAC_CRITERIA_III",
            department_code="CSE", academic_year="2023-2024", output_format="pdf",
            generated_by="coordinator-1", role="coordinator",
        )

        async with session_factory() as session:
            await set_rls_context(session, "CSE", "admin", "verify")
            result = await session.execute(
                text("SELECT status, error_message FROM generated_reports WHERE report_id = :id"),
                {"id": str(report_id)},
            )
            row = dict(result.fetchone()._mapping)

        assert row["status"] == "FAILED"
        assert "unresolved validation error" in row["error_message"]
        # S3 upload must NEVER have been attempted -- the whole point of
        # the gate is to block generation before any rendering/upload work
        assert not mock_s3.upload_file.called


class TestFullGenerationPipelineFacultyProfile:
    async def test_faculty_profile_generation(self, service, session_factory, mock_s3):
        faculty_id = uuid.uuid4()
        async with session_factory() as session:
            await _insert_paper(session, "CSE", faculty_id=str(faculty_id), faculty_email="rajesh@srmap.edu.in")
            report_id = await _insert_report_request(
                session, "FACULTY_PROFILE", "CSE", "2023-2024", "coordinator-1", faculty_id=faculty_id,
            )
            await session.commit()

        await service.run_generation(
            report_id=report_id, report_type="FACULTY_PROFILE",
            department_code="CSE", academic_year="2023-2024", output_format="pdf",
            generated_by="coordinator-1", role="coordinator", faculty_id=faculty_id,
        )

        async with session_factory() as session:
            await set_rls_context(session, "CSE", "admin", "verify")
            result = await session.execute(
                text("SELECT status, total_papers FROM generated_reports WHERE report_id = :id"),
                {"id": str(report_id)},
            )
            row = dict(result.fetchone()._mapping)
        assert row["status"] == "COMPLETED"
        assert row["total_papers"] == 1

    async def test_faculty_profile_scoped_to_correct_faculty_only(self, service, session_factory, mock_s3):
        """Two faculty in the same department -- the profile must only
        include papers for the REQUESTED faculty_id, not all of CSE."""
        faculty_a = uuid.uuid4()
        faculty_b = uuid.uuid4()
        async with session_factory() as session:
            await _insert_paper(session, "CSE", faculty_id=str(faculty_a), title="Paper A")
            await _insert_paper(session, "CSE", faculty_id=str(faculty_b), title="Paper B")
            report_id = await _insert_report_request(
                session, "FACULTY_PROFILE", "CSE", "2023-2024", "coordinator-1", faculty_id=faculty_a,
            )
            await session.commit()

        await service.run_generation(
            report_id=report_id, report_type="FACULTY_PROFILE",
            department_code="CSE", academic_year="2023-2024", output_format="pdf",
            generated_by="coordinator-1", role="coordinator", faculty_id=faculty_a,
        )

        async with session_factory() as session:
            await set_rls_context(session, "CSE", "admin", "verify")
            result = await session.execute(
                text("SELECT total_papers FROM generated_reports WHERE report_id = :id"),
                {"id": str(report_id)},
            )
            assert result.scalar() == 1  # only Paper A, not both
