"""
Module 6 – Reports Routes
POST /reports/generate, GET /reports/{report_id}, GET /reports/download/{report_id}

Per the locked anti-pattern rule: report generation NEVER happens
synchronously inside the request handler. POST /reports/generate
inserts a PENDING row, schedules a BackgroundTask, and returns
immediately. The client polls GET /reports/{report_id} for status.
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import authorize_report_request, get_current_user
from app.config import settings
from app.database import AsyncSessionFactory, get_db, set_rls_context
from app.schemas import (
    ReportDownloadResponse,
    ReportRequest,
    ReportResponse,
    ReportStatusResponse,
    UserContext,
)
from app.services.report_service import ReportService
from app.services.s3_service import S3Service

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])

_s3_service = S3Service(region=settings.AWS_REGION, endpoint_url=settings.AWS_ENDPOINT_URL or None)
_report_service = ReportService(session_factory=AsyncSessionFactory, s3_service=_s3_service)


@router.post("/generate", response_model=ReportResponse, status_code=status.HTTP_202_ACCEPTED)
async def generate_report(
    request: ReportRequest,
    background_tasks: BackgroundTasks,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ReportResponse:
    """
    Kick off report generation as a background task. Returns immediately
    with status=PENDING -- poll GET /reports/{report_id} for completion.
    """
    authorize_report_request(
        user, request.report_type,
        target_faculty_id=str(request.faculty_id) if request.faculty_id else None,
    )

    if request.report_type == "FACULTY_PROFILE" and request.faculty_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="faculty_id is required when report_type=FACULTY_PROFILE",
        )

    await set_rls_context(db, request.department_code, user.role, user.user_id)

    result = await db.execute(
        text("""
            INSERT INTO generated_reports
                (report_type, department_code, academic_year, output_format, faculty_id, generated_by)
            VALUES (:report_type, :dept, :academic_year, :output_format, :faculty_id, :generated_by)
            RETURNING report_id, status, requested_at
        """),
        {
            "report_type": request.report_type,
            "dept": request.department_code,
            "academic_year": request.academic_year,
            "output_format": request.output_format,
            "faculty_id": str(request.faculty_id) if request.faculty_id else None,
            "generated_by": user.user_id,
        },
    )
    row = result.fetchone()
    report_id, initial_status, requested_at = row[0], row[1], row[2]

    background_tasks.add_task(
        _report_service.run_generation,
        report_id=report_id,
        report_type=request.report_type,
        department_code=request.department_code,
        academic_year=request.academic_year,
        output_format=request.output_format,
        generated_by=user.user_id,
        role=user.role,
        faculty_id=request.faculty_id,
    )

    return ReportResponse(
        report_id=report_id,
        report_type=request.report_type,
        status=initial_status,
        department_code=request.department_code,
        academic_year=request.academic_year,
        requested_at=requested_at,
    )


@router.get("/{report_id}", response_model=ReportStatusResponse)
async def get_report_status(
    report_id: UUID,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ReportStatusResponse:
    """Poll the status of a report generation request."""
    await set_rls_context(db, user.department_code, user.role, user.user_id)

    result = await db.execute(
        text("""
            SELECT report_id, report_type, status, department_code, academic_year,
                   s3_uri, checksum_sha256, total_papers, error_message,
                   generated_by, generated_at, requested_at
            FROM generated_reports
            WHERE report_id = :id
        """),
        {"id": str(report_id)},
    )
    row = result.fetchone()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")

    data = dict(row._mapping)

    # Defense in depth beyond RLS: a non-admin user should not see a
    # report generated for a department they don't belong to, even if
    # they somehow have a valid report_id for it (e.g. shared by someone
    # else). RLS on `papers` doesn't cover `generated_reports` (it's
    # Module 6's own table, not Module 4's), so this check is enforced
    # here at the application layer instead.
    if not user.is_admin and data["department_code"] != user.department_code:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")

    return ReportStatusResponse(**data)


@router.get("/download/{report_id}", response_model=ReportDownloadResponse)
async def download_report(
    report_id: UUID,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ReportDownloadResponse:
    """
    Generate a fresh pre-signed S3 URL for a COMPLETED report.
    Never returns the file bytes directly -- always a time-limited URL
    (1hr expiry, per locked spec), generated fresh on every call (not
    cached/reused, since a stale pre-signed URL is just as useless as
    none once it's near expiry).
    """
    await set_rls_context(db, user.department_code, user.role, user.user_id)

    result = await db.execute(
        text("""
            SELECT department_code, status, s3_uri, checksum_sha256
            FROM generated_reports
            WHERE report_id = :id
        """),
        {"id": str(report_id)},
    )
    row = result.fetchone()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")

    data = dict(row._mapping)

    if not user.is_admin and data["department_code"] != user.department_code:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")

    if data["status"] != "COMPLETED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Report is not ready for download (status={data['status']})",
        )

    bucket, key = S3Service.parse_s3_uri(data["s3_uri"])
    download_url = _s3_service.generate_presigned_url(
        bucket=bucket, key=key, expiry_seconds=settings.S3_PRESIGNED_URL_EXPIRY_SECONDS,
    )

    return ReportDownloadResponse(
        report_id=report_id,
        download_url=download_url,
        expires_in_seconds=settings.S3_PRESIGNED_URL_EXPIRY_SECONDS,
        checksum_sha256=data["checksum_sha256"],
    )
