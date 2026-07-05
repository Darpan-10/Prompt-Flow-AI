"""
Module 6 – Unit Tests: Schema Validation
"""

import uuid

import pytest
from pydantic import ValidationError

from app.schemas import ReportRequest, UserContext


class TestReportRequestValidation:
    def test_valid_naac_request(self):
        req = ReportRequest(report_type="NAAC_CRITERIA_III", department_code="CSE", academic_year="2023-2024")
        assert req.output_format == "pdf"  # default

    def test_valid_faculty_profile_request(self):
        fid = uuid.uuid4()
        req = ReportRequest(
            report_type="FACULTY_PROFILE", department_code="CSE",
            academic_year="2023-2024", faculty_id=fid,
        )
        assert req.faculty_id == fid

    def test_academic_year_must_span_one_year(self):
        with pytest.raises(ValidationError):
            ReportRequest(report_type="NAAC_CRITERIA_III", department_code="CSE", academic_year="2023-2025")

    def test_academic_year_must_match_pattern(self):
        with pytest.raises(ValidationError):
            ReportRequest(report_type="NAAC_CRITERIA_III", department_code="CSE", academic_year="23-24")
        with pytest.raises(ValidationError):
            ReportRequest(report_type="NAAC_CRITERIA_III", department_code="CSE", academic_year="2023")

    def test_academic_year_descending_rejected(self):
        with pytest.raises(ValidationError):
            ReportRequest(report_type="NAAC_CRITERIA_III", department_code="CSE", academic_year="2024-2023")

    def test_invalid_report_type_rejected(self):
        with pytest.raises(ValidationError):
            ReportRequest(report_type="SOMETHING_ELSE", department_code="CSE", academic_year="2023-2024")  # type: ignore[arg-type]

    def test_invalid_output_format_rejected(self):
        with pytest.raises(ValidationError):
            ReportRequest(
                report_type="NAAC_CRITERIA_III", department_code="CSE",
                academic_year="2023-2024", output_format="docx",  # type: ignore[arg-type]
            )

    def test_empty_department_code_rejected(self):
        with pytest.raises(ValidationError):
            ReportRequest(report_type="NAAC_CRITERIA_III", department_code="", academic_year="2023-2024")

    def test_faculty_id_optional_for_naac(self):
        req = ReportRequest(report_type="NAAC_CRITERIA_III", department_code="CSE", academic_year="2023-2024")
        assert req.faculty_id is None


class TestUserContext:
    def test_valid_roles(self):
        for role in ["faculty", "coordinator", "hod", "admin", "system_worker"]:
            ctx = UserContext(user_id="u1", department_code="CSE", role=role)
            assert ctx.role == role

    def test_invalid_role_rejected(self):
        with pytest.raises(ValidationError):
            UserContext(user_id="u1", department_code="CSE", role="superadmin")  # type: ignore[arg-type]

    def test_is_admin_flag_independent_of_role(self):
        """is_admin is a separate field set explicitly by the auth
        dependency, not auto-derived by Pydantic -- this test documents
        that distinction (the auth code is responsible for setting it
        correctly based on role=='admin')."""
        ctx = UserContext(user_id="u1", department_code="CSE", role="faculty", is_admin=False)
        assert ctx.is_admin is False
