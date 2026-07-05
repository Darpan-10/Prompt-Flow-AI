"""
Module 6 – Report Service
Deterministic report generation: compliance gate -> fetch verified data
-> render Jinja2 template -> PDF (WeasyPrint) or Excel (openpyxl) ->
SHA-256 checksum -> S3 upload -> persist metadata + audit log.

NO LLM CALLS ANYWHERE IN THIS FILE. Every report produced from the same
input data produces byte-identical output (modulo the `generated_at`
timestamp baked into the template) -- that determinism is the entire
point of this module existing separately from Modules 1-3's AI pipeline.
"""

from __future__ import annotations

import io
import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

import orjson
from jinja2 import Environment, FileSystemLoader, select_autoescape
from openpyxl import Workbook
from openpyxl.styles import Font
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from weasyprint import HTML

from app.config import settings
from app.database import set_rls_context
from app.services.checksum_service import ChecksumService
from app.services.s3_service import S3Service

log = logging.getLogger(__name__)


class ComplianceGateError(Exception):
    """Raised when source data doesn't pass the compliance gate. Carries
    enough structure for the API layer to return a useful 422."""

    def __init__(self, message: str, unresolved_error_count: int = 0, pending_paper_count: int = 0):
        super().__init__(message)
        self.unresolved_error_count = unresolved_error_count
        self.pending_paper_count = pending_paper_count


def _split_academic_year(academic_year: str) -> tuple[int, int]:
    start, end = academic_year.split("-")
    return int(start), int(end)


class ReportService:
    def __init__(self, session_factory: async_sessionmaker, s3_service: Optional[S3Service] = None):
        """
        Takes a SESSION FACTORY, not a single session -- report
        generation runs as a FastAPI BackgroundTask, which executes
        AFTER the HTTP response has already been returned. The
        request-scoped session (and its dependency-injected lifecycle)
        is long gone by then; this service must open its own fresh
        session for the background work.
        """
        self.session_factory = session_factory
        self.s3 = s3_service or S3Service(region=settings.AWS_REGION, endpoint_url=settings.AWS_ENDPOINT_URL or None)
        self.checksum = ChecksumService()
        self.jinja_env = Environment(
            loader=FileSystemLoader(settings.REPORT_TEMPLATE_DIR),
            autoescape=select_autoescape(["html"]),
        )

    # -- Compliance gate -----------------------------------------------------

    async def _check_compliance_gate(
        self,
        session: AsyncSession,
        department_code: str,
        year_start: int,
        year_end: int,
    ) -> None:
        """
        Two independent checks, BOTH must pass:

        1. No unresolved error-severity validation_issues for papers in
           scope (exact query from the locked spec).
        2. No papers in scope still sitting in PENDING_REVIEW or DRAFT
           (i.e. not yet finalized one way or the other). REJECTED papers
           do NOT count against this -- a rejection is itself a final,
           deliberate outcome, not an "unresolved" state; gating on it
           would make this check impossible to ever pass for any
           department with normal ingestion noise.
        """
        error_count = await session.scalar(
            text("""
                SELECT COUNT(*) FROM validation_issues vi
                JOIN papers p ON vi.paper_id = p.paper_id
                WHERE p.department_code = :dept
                  AND p.year BETWEEN :year_start AND :year_end
                  AND vi.severity = 'error'
                  AND vi.resolved_at IS NULL
            """),
            {"dept": department_code, "year_start": year_start, "year_end": year_end},
        )

        pending_count = await session.scalar(
            text("""
                SELECT COUNT(*) FROM papers
                WHERE department_code = :dept
                  AND year BETWEEN :year_start AND :year_end
                  AND status IN ('PENDING_REVIEW', 'DRAFT')
            """),
            {"dept": department_code, "year_start": year_start, "year_end": year_end},
        )

        if error_count > 0 or pending_count > 0:
            parts = []
            if error_count > 0:
                parts.append(f"{error_count} unresolved validation error(s)")
            if pending_count > 0:
                parts.append(f"{pending_count} paper(s) still PENDING_REVIEW/DRAFT")
            raise ComplianceGateError(
                f"Cannot generate report: {' and '.join(parts)} in scope for "
                f"{department_code} {year_start}-{year_end}.",
                unresolved_error_count=error_count,
                pending_paper_count=pending_count,
            )

    # -- NAAC Criteria III -----------------------------------------------------

    async def _fetch_naac_criteria_iii_data(
        self,
        session: AsyncSession,
        department_code: str,
        year_start: int,
        year_end: int,
    ) -> list[dict[str, Any]]:
        result = await session.execute(
            text("""
                SELECT title, authors, venue, year, doi, paper_type,
                       overall_confidence, faculty_email
                FROM papers
                WHERE department_code = :dept
                  AND year BETWEEN :year_start AND :year_end
                  AND status = 'PUBLISHED'
                ORDER BY year DESC, title ASC
            """),
            {"dept": department_code, "year_start": year_start, "year_end": year_end},
        )
        return [dict(row._mapping) for row in result.fetchall()]

    def _render_naac_criteria_iii_pdf(
        self,
        department_code: str,
        academic_year: str,
        papers: list[dict[str, Any]],
    ) -> bytes:
        template = self.jinja_env.get_template("naac_criteria_iii.html")
        html_content = template.render(
            department=department_code,
            academic_year=academic_year,
            papers=papers,
            generated_at=datetime.now(timezone.utc).isoformat(),
            total_papers=len(papers),
            journal_count=sum(1 for p in papers if p["paper_type"] == "journal"),
            conference_count=sum(1 for p in papers if p["paper_type"] == "conference"),
        )
        return HTML(string=html_content).write_pdf()

    def _render_naac_criteria_iii_xlsx(
        self,
        department_code: str,
        academic_year: str,
        papers: list[dict[str, Any]],
    ) -> bytes:
        wb = Workbook()
        ws = wb.active
        ws.title = "NAAC Criteria III"

        header_font = Font(bold=True)
        headers = ["Title", "Authors", "Venue", "Year", "DOI", "Type", "Confidence", "Faculty Email"]
        ws.append(headers)
        for cell in ws[1]:
            cell.font = header_font

        for p in papers:
            author_names = ", ".join(a.get("name", "Unknown") for a in (p.get("authors") or []))
            ws.append([
                p["title"], author_names, p.get("venue") or "", p["year"],
                p.get("doi") or "", p["paper_type"], float(p["overall_confidence"]),
                p["faculty_email"],
            ])

        for col_cells in ws.columns:
            max_len = max((len(str(c.value)) for c in col_cells if c.value is not None), default=10)
            ws.column_dimensions[col_cells[0].column_letter].width = min(max_len + 2, 60)

        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    # -- Faculty Profile -----------------------------------------------------

    async def _fetch_faculty_profile_data(
        self,
        session: AsyncSession,
        department_code: str,
        faculty_id: UUID,
        year_start: int,
        year_end: int,
    ) -> list[dict[str, Any]]:
        result = await session.execute(
            text("""
                SELECT title, authors, venue, year, doi, paper_type,
                       overall_confidence, faculty_email
                FROM papers
                WHERE department_code = :dept
                  AND faculty_id = :faculty_id
                  AND year BETWEEN :year_start AND :year_end
                  AND status = 'PUBLISHED'
                ORDER BY year DESC, title ASC
            """),
            {"dept": department_code, "faculty_id": str(faculty_id), "year_start": year_start, "year_end": year_end},
        )
        return [dict(row._mapping) for row in result.fetchall()]

    def _render_faculty_profile_pdf(
        self,
        department_code: str,
        academic_year: str,
        faculty_email: str,
        papers: list[dict[str, Any]],
    ) -> bytes:
        template = self.jinja_env.get_template("faculty_profile.html")
        html_content = template.render(
            department=department_code,
            academic_year=academic_year,
            faculty_email=faculty_email,
            papers=papers,
            generated_at=datetime.now(timezone.utc).isoformat(),
            total_papers=len(papers),
            journal_count=sum(1 for p in papers if p["paper_type"] == "journal"),
            conference_count=sum(1 for p in papers if p["paper_type"] == "conference"),
            avg_confidence=(
                round(sum(float(p["overall_confidence"]) for p in papers) / len(papers), 3)
                if papers else 0.0
            ),
        )
        return HTML(string=html_content).write_pdf()

    # -- Orchestration (called from the background task) ---------------------

    async def run_generation(
        self,
        report_id: UUID,
        report_type: str,
        department_code: str,
        academic_year: str,
        output_format: str,
        generated_by: str,
        role: str,
        faculty_id: Optional[UUID] = None,
    ) -> None:
        """
        The actual background task body. Opens its OWN session (the
        request-scoped one is gone by the time this runs), sets RLS
        context, runs the full generate -> render -> upload -> persist
        pipeline, and updates the generated_reports row's status
        regardless of success or failure -- a report stuck at PENDING or
        GENERATING forever (because an exception was swallowed silently)
        is its own kind of bug.
        """
        async with self.session_factory() as session:
            try:
                await set_rls_context(session, department_code, role, generated_by)
                await session.execute(
                    text("UPDATE generated_reports SET status = 'GENERATING' WHERE report_id = :id"),
                    {"id": str(report_id)},
                )
                await session.commit()

                # CRITICAL: set_rls_context() uses set_config(..., true)
                # ('is_local' = true), which is the function-call
                # equivalent of SET LOCAL -- its effect is scoped to the
                # CURRENT TRANSACTION ONLY. The session.commit() above
                # ends that transaction, silently clearing the RLS
                # context for every query that follows in this same
                # session. Without re-establishing it here, the
                # compliance gate query below would see ZERO rows (not
                # an error) for BOTH the unresolved-errors check and the
                # pending-papers check -- meaning it would ALWAYS pass
                # trivially, regardless of real data state, and the
                # subsequent data-fetch query would always return zero
                # papers. This is exactly the dangerous RLS false-negative
                # documented in app/database.py's set_rls_context()
                # docstring -- caught here by
                # tests/integration/test_full_pipeline.py running against
                # a REAL PostgreSQL instance (a mocked DB session would
                # never have caught this, since the bug is in how a real
                # transaction boundary interacts with a real RLS policy).
                await set_rls_context(session, department_code, role, generated_by)

                year_start, year_end = _split_academic_year(academic_year)
                await self._check_compliance_gate(session, department_code, year_start, year_end)

                file_bytes: bytes
                content_type: str
                total_papers: int
                s3_key_suffix: str

                if report_type == "NAAC_CRITERIA_III":
                    papers = await self._fetch_naac_criteria_iii_data(session, department_code, year_start, year_end)
                    total_papers = len(papers)
                    if output_format == "xlsx":
                        file_bytes = self._render_naac_criteria_iii_xlsx(department_code, academic_year, papers)
                        content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                        s3_key_suffix = "xlsx"
                    else:
                        file_bytes = self._render_naac_criteria_iii_pdf(department_code, academic_year, papers)
                        content_type = "application/pdf"
                        s3_key_suffix = "pdf"
                    s3_key_prefix = "criteria_iii"

                elif report_type == "FACULTY_PROFILE":
                    if faculty_id is None:
                        raise ValueError("faculty_id is required for FACULTY_PROFILE reports")
                    papers = await self._fetch_faculty_profile_data(
                        session, department_code, faculty_id, year_start, year_end
                    )
                    total_papers = len(papers)
                    faculty_email = papers[0]["faculty_email"] if papers else "unknown@srmap.edu.in"
                    file_bytes = self._render_faculty_profile_pdf(department_code, academic_year, faculty_email, papers)
                    content_type = "application/pdf"
                    s3_key_suffix = "pdf"
                    s3_key_prefix = "faculty_profile"

                else:
                    raise ValueError(f"Unknown report_type: {report_type}")

                checksum = self.checksum.calculate_sha256(file_bytes)

                timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                s3_key = f"reports/{department_code}/{academic_year}/{s3_key_prefix}_{timestamp}.{s3_key_suffix}"
                s3_uri = await self.s3.upload_file(
                    bucket=settings.S3_REPORTS_BUCKET,
                    key=s3_key,
                    file_bytes=file_bytes,
                    content_type=content_type,
                )

                await session.execute(
                    text("""
                        UPDATE generated_reports
                        SET status = 'COMPLETED', s3_uri = :s3_uri,
                            checksum_sha256 = :checksum, total_papers = :total_papers,
                            generated_at = NOW()
                        WHERE report_id = :id
                    """),
                    {"s3_uri": s3_uri, "checksum": checksum, "total_papers": total_papers, "id": str(report_id)},
                )

                await session.execute(
                    text("""
                        INSERT INTO report_checksums (report_id, checksum_sha256, event_type)
                        VALUES (:report_id, :checksum, 'GENERATED')
                    """),
                    {"report_id": str(report_id), "checksum": checksum},
                )

                await session.execute(
                    text("""
                        INSERT INTO audit_log (action, actor_type, actor_id, resource_type, resource_id, details)
                        VALUES ('report_generated', 'user', :actor_id, 'report', :resource_id, :details)
                    """),
                    {
                        "actor_id": generated_by,
                        "resource_id": str(report_id),
                        # CRITICAL: asyncpg (unlike psycopg2) does NOT
                        # auto-serialize a Python dict to JSON for a JSONB
                        # column when the query goes through SQLAlchemy's
                        # raw text() + bind-parameters path (only the
                        # ORM's JSONB column TYPE does that automatically).
                        # Passing a raw dict here raises
                        # "asyncpg.exceptions.DataError: invalid input
                        # ... 'dict' object has no attribute 'encode'" --
                        # caught by tests/integration/test_full_pipeline.py
                        # against a real PostgreSQL instance. Must
                        # explicitly serialize to a JSON string first;
                        # PostgreSQL then implicitly casts text -> jsonb
                        # on insert. (Reading JSONB back via SELECT is
                        # NOT affected -- asyncpg auto-decodes JSONB to a
                        # native Python list/dict on the way out
                        # regardless of which path wrote it, which is why
                        # this asymmetry between read and write is easy
                        # to miss without testing against a real DB.)
                        "details": orjson.dumps({
                            "type": report_type,
                            "dept": department_code,
                            "academic_year": academic_year,
                            "checksum": checksum,
                            "total_papers": total_papers,
                        }).decode(),
                    },
                )

                await session.commit()
                log.info(
                    "Report generated: report_id=%s type=%s dept=%s papers=%d checksum=%s",
                    report_id, report_type, department_code, total_papers, checksum[:12],
                )

            except ComplianceGateError as exc:
                await session.rollback()
                async with self.session_factory() as fail_session:
                    await set_rls_context(fail_session, department_code, role, generated_by)
                    await fail_session.execute(
                        text("UPDATE generated_reports SET status = 'FAILED', error_message = :msg WHERE report_id = :id"),
                        {"msg": str(exc), "id": str(report_id)},
                    )
                    await fail_session.commit()
                log.warning("Report generation blocked by compliance gate: report_id=%s reason=%s", report_id, exc)

            except Exception as exc:
                await session.rollback()
                async with self.session_factory() as fail_session:
                    await set_rls_context(fail_session, department_code, role, generated_by)
                    await fail_session.execute(
                        text("UPDATE generated_reports SET status = 'FAILED', error_message = :msg WHERE report_id = :id"),
                        {"msg": str(exc)[:2000], "id": str(report_id)},
                    )
                    await fail_session.commit()
                log.error("Report generation failed unexpectedly: report_id=%s", report_id, exc_info=True)
