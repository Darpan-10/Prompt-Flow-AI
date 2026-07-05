"""
Module 6 – Schemas
Pydantic V2 models for report generation requests/responses.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


# -- Request models --------------------------------------------------------

class ReportRequest(BaseModel):
    """
    POST /reports/generate body.

    academic_year must be "YYYY-YYYY" (e.g. "2023-2024") -- the
    ReportService splits this into a start/end year for the BETWEEN
    query against papers.year.
    """
    report_type: Literal["NAAC_CRITERIA_III", "FACULTY_PROFILE"]
    department_code: str = Field(..., min_length=1, max_length=20)
    academic_year: str = Field(..., pattern=r"^\d{4}-\d{4}$")
    output_format: Literal["pdf", "xlsx"] = "pdf"

    # Only used for FACULTY_PROFILE -- which faculty member to generate
    # the profile for. Required for that report_type, ignored otherwise.
    faculty_id: Optional[UUID] = None

    @field_validator("academic_year")
    @classmethod
    def validate_year_range(cls, v: str) -> str:
        start, end = v.split("-")
        if int(end) != int(start) + 1:
            raise ValueError(
                f"academic_year must span exactly one year (e.g. '2023-2024'), got '{v}'"
            )
        return v


# -- Response models --------------------------------------------------------

class ReportResponse(BaseModel):
    """Returned immediately by POST /reports/generate -- generation runs
    as a background task, so this reflects PENDING status at first."""
    report_id: UUID
    report_type: str
    status: Literal["PENDING", "GENERATING", "COMPLETED", "FAILED"]
    department_code: str
    academic_year: str
    requested_at: datetime
    message: str = "Report generation started. Poll GET /reports/{report_id} for status."


class ReportStatusResponse(BaseModel):
    """Returned by GET /reports/{report_id}."""
    report_id: UUID
    report_type: str
    status: Literal["PENDING", "GENERATING", "COMPLETED", "FAILED"]
    department_code: str
    academic_year: str
    s3_uri: Optional[str] = None
    checksum_sha256: Optional[str] = None
    total_papers: Optional[int] = None
    error_message: Optional[str] = None
    generated_by: str
    generated_at: Optional[datetime] = None
    requested_at: datetime


class ReportDownloadResponse(BaseModel):
    """Returned by GET /reports/download/{report_id} -- a pre-signed URL,
    NEVER the file bytes themselves (per locked spec: 1hr expiry)."""
    report_id: UUID
    download_url: str
    expires_in_seconds: int
    checksum_sha256: str


class ComplianceGateErrorResponse(BaseModel):
    """
    Shape of the 422 response when the compliance gate blocks generation
    (unresolved error-severity validation_issues, or papers still
    PENDING_REVIEW/DRAFT, in scope).
    """
    error: str = "compliance_gate_failed"
    unresolved_error_count: int
    pending_paper_count: int
    department_code: str
    academic_year: str
    detail: str


# -- Auth Context (from JWT, used internally) -------------------------------

class UserContext(BaseModel):
    """
    User context extracted from JWT claims (issued by Module 1).
    Same shape as Module 4/5's equivalent -- each module independently
    trusts the same claim names rather than importing a shared library.
    """
    user_id: str
    department_code: str
    role: Literal["faculty", "coordinator", "hod", "admin", "system_worker"]
    faculty_id: Optional[UUID] = None
    is_admin: bool = False
