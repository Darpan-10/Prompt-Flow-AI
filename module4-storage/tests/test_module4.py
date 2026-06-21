"""
Module 4 – Test Suite
Tests: repository CRUD, RLS context, Kafka consumer logic, API endpoints.
Run: pytest tests/ -v
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.schemas import (
    Author,
    Attachment,
    FullTextSearchResult,
    KafkaPayload,
    PaperCreate,
    PaperUpdate,
    ValidationIssueCreate,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_sha256(text: str = "test") -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def make_paper_create(
    *,
    idem_key: str | None = None,
    status: str = "PUBLISHED",
    dept: str = "CSE",
    year: int = 2024,
    doi: str | None = "10.1145/test.001",
) -> PaperCreate:
    return PaperCreate(
        ingestion_idempotency_key=(idem_key or make_sha256(str(uuid.uuid4()))),
        extraction_id=uuid.uuid4(),
        title="A Novel Approach to Deep Learning for Academic Paper Classification",
        authors=[
            Author(name="Dr. Venkata Rao", affiliation="SRM AP University", order=1),
            Author(name="Prof. Jane Smith", affiliation="SRM AP CSE", order=2),
        ],
        venue="ACM SIGCHI 2024",
        year=year,
        doi=doi,
        paper_type="conference",
        faculty_id=uuid.uuid4(),
        faculty_email="dr.rao@srmap.edu.in",
        department_code=dept,
        status=status,
        overall_confidence=0.92,
        raw_text_hash=make_sha256("paper content"),
        attachment_uris=[
            Attachment(
                filename="paper.pdf",
                uri="s3://promptflow-ingestion-dev/papers/paper.pdf",
                checksum_sha256=make_sha256("pdf bytes"),
            )
        ],
    )


def make_kafka_payload(
    action: str = "AUTO_SAVE",
    dept: str = "CSE",
    has_issues: bool = False,
) -> Dict[str, Any]:
    """
    Builds a payload matching the REAL Module 3 schema, confirmed against
    an actual papers.validated sample message on 2026-06-19.

    Key real-schema facts baked in here:
      - idempotency_key and extraction_id are TOP-LEVEL fields
      - routing_decision.final_action (not "action")
      - audit_trail.trace_id is W3C traceparent format
      - there is no "email" envelope and no top-level "created_at"
    """
    idem = make_sha256(str(uuid.uuid4()))
    return {
        "extraction_id": str(uuid.uuid4()),
        "event_id": str(uuid.uuid4()),
        "idempotency_key": idem,
        "contract_version": "v1",
        "routing_decision": {
            "pipeline_status": "validated",
            "final_action": action,
            "overall_confidence": 0.88,
            "applied_threshold": 0.85,
            "decision_rationale": {
                "summary": "Confidence above threshold",
                "details": {
                    "review_issues": [],
                    "blocking_issues": [],
                    "confidence_gap": 0.03,
                    "warnings_within_tolerance": True,
                },
            },
        },
        "extraction_result": {
            "metadata": {
                "title": "Machine Learning in Healthcare: A Comprehensive Review",
                "authors": [{"name": "Dr. Rao", "affiliation": "SRM AP", "order": 1}],
                "venue": "Neural Networks 2024",
                "year": 2024,
                "doi": "10.1016/j.neunet.2024.01.001",
                "paper_type": "journal",
            },
            "confidence_scores": {
                "title": 0.95, "authors": 0.90, "venue": 0.85,
                "year": 1.0, "doi": 1.0, "paper_type": 0.90,
            },
            "extraction_source": "CROSSREF_API",
            "embedding": None,
        },
        "enriched_context": {
            "faculty_id": str(uuid.uuid4()),
            "faculty_name": "Dr. Venkata Rao",
            "faculty_email": "dr.rao@srmap.edu.in",
            "department_code": dept,
            "faculty_status": "active",
        },
        "content_reference": {
            "raw_text_hash": make_sha256("paper text"),
            "attachments": [
                {
                    "filename": "paper.pdf",
                    "uri": "s3://bucket/paper.pdf",
                    "checksum_sha256": make_sha256("pdf"),
                }
            ],
        },
        "validation_issues": (
            [
                {
                    "code": "LOW_CONFIDENCE_TITLE",
                    "severity": "warning",
                    "action": "REVIEW_REQUIRED",
                    "source": "tier3_nlp",
                    "message": "Title confidence below threshold",
                    "confidence": 0.62,
                    "threshold": 0.70,
                }
            ]
            if has_issues
            else []
        ),
        "audit_trail": {
            "trace_id": "00-1234567890abcdef1234567890abcdef-1234567890abcdef-01",
            "span_id": "abcdef1234567890",
            "processing_times": {
                "regex_ms": 12, "crossref_ms": 340, "directory_ms": 45,
                "routing_ms": 3, "total_ms": 400,
            },
        },
        "processing": {
            "attempts": [
                {
                    "attempt_number": 1,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "status": "success",
                    "source": "CROSSREF_API",
                }
            ],
        },
    }


# ── Schema tests ──────────────────────────────────────────────────────────────

class TestPaperCreate:
    def test_valid_paper(self):
        p = make_paper_create()
        assert p.title
        assert len(p.authors) == 2
        assert p.overall_confidence == 0.92

    def test_embedding_dimension_768(self):
        embedding = [0.1] * 768
        p = make_paper_create()
        p2 = p.model_copy(update={"embedding": embedding})
        assert len(p2.embedding) == 768

    def test_embedding_wrong_dimension_raises(self):
        import pytest
        with pytest.raises(Exception):
            PaperCreate(
                ingestion_idempotency_key=make_sha256("x"),
                extraction_id=uuid.uuid4(),
                title="A valid title that is long enough for the constraint",
                authors=[Author(name="Author", order=1)],
                year=2024,
                paper_type="journal",
                faculty_id=uuid.uuid4(),
                faculty_email="x@srmap.edu.in",
                department_code="CSE",
                status="PUBLISHED",
                overall_confidence=0.9,
                raw_text_hash=make_sha256("x"),
                attachment_uris=[],
                embedding=[0.1] * 500,  # wrong dimension
            )

    def test_confidence_out_of_range(self):
        import pytest
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            make_paper_create().__class__(
                **{**make_paper_create().model_dump(), "overall_confidence": 1.5}
            )

    def test_sha256_pattern_enforced(self):
        import pytest
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            PaperCreate(
                ingestion_idempotency_key="not_a_hash",
                extraction_id=uuid.uuid4(),
                title="A valid title that is long enough",
                authors=[Author(name="Author", order=1)],
                year=2024,
                paper_type="journal",
                faculty_id=uuid.uuid4(),
                faculty_email="x@srmap.edu.in",
                department_code="CSE",
                status="PUBLISHED",
                overall_confidence=0.9,
                raw_text_hash="NOT_VALID_SHA256",  # bad hash
                attachment_uris=[],
            )


class TestPaperUpdate:
    def test_update_requires_change_reason(self):
        import pytest
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            PaperUpdate(title="New Title")  # missing change_reason

    def test_valid_update(self):
        u = PaperUpdate(
            status="PUBLISHED",
            change_reason="Coordinator approved after review",
        )
        assert u.status == "PUBLISHED"
        assert u.change_reason


class TestValidationIssueCreate:
    def test_valid_issue(self):
        issue = ValidationIssueCreate(
            paper_id=uuid.uuid4(),
            issue_code="LOW_CONFIDENCE_TITLE",
            severity="warning",
            action="REVIEW_REQUIRED",
            source="tier3_nlp",
            message="Title confidence below threshold",
            confidence=0.62,
            threshold=0.70,
        )
        assert issue.severity == "warning"
        assert issue.action == "REVIEW_REQUIRED"

    def test_invalid_severity(self):
        import pytest
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ValidationIssueCreate(
                paper_id=uuid.uuid4(),
                issue_code="X",
                severity="critical",  # not allowed
                action="BLOCK",
                source="test",
                message="test",
            )


# ── KafkaPayload tests ────────────────────────────────────────────────────────

class TestKafkaPayload:
    def test_parse_valid_payload(self):
        raw = make_kafka_payload()
        payload = KafkaPayload.model_validate(raw)
        assert payload.event_id
        assert payload.department_code == "CSE"

    def test_resolved_status_published(self):
        raw = make_kafka_payload(action="AUTO_SAVE")
        payload = KafkaPayload.model_validate(raw)
        assert payload.resolved_status == "PUBLISHED"

    def test_resolved_status_pending_review(self):
        raw = make_kafka_payload(action="REVIEW_REQUIRED")
        payload = KafkaPayload.model_validate(raw)
        assert payload.resolved_status == "PENDING_REVIEW"

    def test_resolved_status_rejected(self):
        raw = make_kafka_payload(action="BLOCK")
        payload = KafkaPayload.model_validate(raw)
        assert payload.resolved_status == "REJECTED"

    def test_idempotency_key_top_level(self):
        raw = make_kafka_payload()
        payload = KafkaPayload.model_validate(raw)
        assert payload.idempotency_key == raw["idempotency_key"]

    def test_validation_issues_parsed(self):
        raw = make_kafka_payload(has_issues=True)
        payload = KafkaPayload.model_validate(raw)
        assert len(payload.validation_issues) == 1
        assert payload.validation_issues[0]["severity"] == "warning"


# ── Consumer logic tests ──────────────────────────────────────────────────────

class TestConsumerLogic:
    def test_map_topic_to_status_auto_save(self):
        from app.consumer import _map_topic_to_status
        assert _map_topic_to_status("papers.validated", "AUTO_SAVE") == "PUBLISHED"

    def test_map_topic_to_status_review(self):
        from app.consumer import _map_topic_to_status
        assert _map_topic_to_status("papers.review", "REVIEW_REQUIRED") == "PENDING_REVIEW"

    def test_map_topic_to_status_block(self):
        from app.consumer import _map_topic_to_status
        assert _map_topic_to_status("papers.failed", "BLOCK") == "REJECTED"

    def test_build_paper_create_from_payload(self):
        from app.consumer import _build_paper_create
        raw = make_kafka_payload(action="AUTO_SAVE")
        payload = KafkaPayload.model_validate(raw)
        with patch("app.consumer.generate_embedding", return_value=[0.01] * 768) as mock_embed:
            paper = _build_paper_create(payload, "papers.validated")
        assert paper.status == "PUBLISHED"
        assert paper.department_code == "CSE"
        assert paper.year == 2024
        assert paper.paper_type == "journal"
        assert mock_embed.called  # PUBLISHED papers get embedded
        assert len(paper.embedding) == 768

    def test_build_paper_create_review_skips_embedding(self):
        """REVIEW_REQUIRED papers should NOT trigger embedding generation (cost optimization)."""
        from app.consumer import _build_paper_create
        raw = make_kafka_payload(action="REVIEW_REQUIRED")
        payload = KafkaPayload.model_validate(raw)
        with patch("app.consumer.generate_embedding", return_value=[0.01] * 768) as mock_embed:
            paper = _build_paper_create(payload, "papers.review")
        assert paper.status == "PENDING_REVIEW"
        assert not mock_embed.called
        assert paper.embedding is None

    def test_build_paper_create_block_status(self):
        from app.consumer import _build_paper_create
        raw = make_kafka_payload(action="BLOCK")
        payload = KafkaPayload.model_validate(raw)
        paper = _build_paper_create(payload, "papers.failed")
        assert paper.status == "REJECTED"

    def test_build_validation_issues(self):
        from app.consumer import _build_validation_issues
        raw = make_kafka_payload(has_issues=True)
        payload = KafkaPayload.model_validate(raw)
        paper_id = uuid.uuid4()
        issues = _build_validation_issues(payload, paper_id)
        assert len(issues) == 1
        assert issues[0].severity == "warning"
        assert issues[0].paper_id == paper_id

    def test_build_validation_issues_empty(self):
        from app.consumer import _build_validation_issues
        raw = make_kafka_payload(has_issues=False)
        payload = KafkaPayload.model_validate(raw)
        issues = _build_validation_issues(payload, uuid.uuid4())
        assert issues == []

    def test_idempotency_key_normalisation(self):
        from app.consumer import _build_paper_create
        raw = make_kafka_payload()
        # Ensure short key gets padded
        raw["idempotency_key"] = "short_key"
        payload = KafkaPayload.model_validate(raw)
        with patch("app.consumer.generate_embedding", return_value=[0.01] * 768):
            paper = _build_paper_create(payload, "papers.validated")
        assert len(paper.ingestion_idempotency_key) == 64


# ── Real fixture regression test ─────────────────────────────────────────────
# Locks in parsing against an ACTUAL sample message captured from
# papers.validated on 2026-06-19. If Module 3's schema ever drifts,
# this test fails immediately instead of silently breaking ingestion.

class TestRealModule3Fixture:
    @pytest.fixture
    def real_payload(self):
        import json
        from pathlib import Path
        fixture_path = Path(__file__).parent / "fixtures" / "real_sample_validated.json"
        with open(fixture_path) as f:
            raw = json.load(f)
        return KafkaPayload.model_validate(raw)

    def test_parses_without_error(self, real_payload):
        assert real_payload.extraction_id == "b7f3a912-4e5c-4d8a-9f21-3c6e8a7b5d42"

    def test_department_code(self, real_payload):
        assert real_payload.department_code == "CSE"

    def test_faculty_email(self, real_payload):
        assert real_payload.faculty_email == "rajesh.kumar@srmap.edu.in"

    def test_final_action_and_resolved_status(self, real_payload):
        assert real_payload.final_action == "AUTO_SAVE"
        assert real_payload.resolved_status == "PUBLISHED"

    def test_overall_confidence(self, real_payload):
        assert real_payload.overall_confidence == 0.94

    def test_trace_id_parsed_from_w3c_traceparent(self, real_payload):
        # "00-1234567890abcdef1234567890abcdef-1234567890abcdef-01"
        assert real_payload.trace_id_uuid == "12345678-90ab-cdef-1234-567890abcdef"

    def test_metadata_fields(self, real_payload):
        meta = real_payload.extraction_result["metadata"]
        assert meta["title"] == "Attention Is All You Need"
        assert meta["doi"] == "10.48550/arXiv.1706.03762"
        assert meta["year"] == 2017
        assert len(meta["authors"]) == 2

    def test_attachments_present(self, real_payload):
        attachments = real_payload.content_reference["attachments"]
        assert len(attachments) == 1
        assert attachments[0]["filename"] == "vaswani2017attention.pdf"

    def test_full_build_paper_create_pipeline(self, real_payload):
        """End-to-end: real payload -> PaperCreate, with embedding mocked."""
        from app.consumer import _build_paper_create
        with patch("app.consumer.generate_embedding", return_value=[0.02] * 768) as mock_embed:
            paper = _build_paper_create(real_payload, "papers.validated")

        assert paper.status == "PUBLISHED"
        assert paper.title == "Attention Is All You Need"
        assert paper.department_code == "CSE"
        assert paper.doi == "10.48550/arXiv.1706.03762"
        assert mock_embed.called
        mock_embed.assert_called_once_with(
            title="Attention Is All You Need",
            venue="Advances in Neural Information Processing Systems",
        )
        assert len(paper.embedding) == 768


# ── Routing / Status tests ────────────────────────────────────────────────────

class TestStatusRouting:
    @pytest.mark.parametrize("action,expected_status", [
        ("AUTO_SAVE",        "PUBLISHED"),
        ("REVIEW_REQUIRED",  "PENDING_REVIEW"),
        ("BLOCK",            "REJECTED"),
        ("UNKNOWN",          "DRAFT"),
    ])
    def test_all_routing_paths(self, action, expected_status):
        from app.consumer import _map_topic_to_status
        result = _map_topic_to_status("papers.validated", action)
        assert result == expected_status


# ── Confidence + metadata tests ───────────────────────────────────────────────

class TestMetadataIntegrity:
    def test_authors_list_not_empty(self):
        p = make_paper_create()
        assert len(p.authors) >= 1

    def test_attachment_checksum_format(self):
        a = Attachment(
            filename="paper.pdf",
            uri="s3://bucket/paper.pdf",
            checksum_sha256=make_sha256("pdf_bytes"),
        )
        assert len(a.checksum_sha256) == 64
        assert all(c in "0123456789abcdef" for c in a.checksum_sha256)

    def test_paper_type_enum(self):
        import pytest
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            p = make_paper_create()
            p.__class__(**{**p.model_dump(), "paper_type": "poster"})  # invalid

    def test_confidence_range_boundaries(self):
        p0 = make_paper_create()
        p0 = p0.__class__(**{**p0.model_dump(), "overall_confidence": 0.0})
        assert p0.overall_confidence == 0.0

        p1 = make_paper_create()
        p1 = p1.__class__(**{**p1.model_dump(), "overall_confidence": 1.0})
        assert p1.overall_confidence == 1.0

    def test_year_boundary(self):
        import datetime
        import pytest
        from pydantic import ValidationError
        max_year = datetime.datetime.now().year + 1
        with pytest.raises(ValidationError):
            p = make_paper_create()
            p.__class__(**{**p.model_dump(), "year": max_year + 5})
