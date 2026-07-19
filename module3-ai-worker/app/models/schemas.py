"""
Pydantic v2 schemas for Module 3.

IngestedPayload   — input from Kafka topic ingest.raw (Module 2 output)
PaperExtractedV1  — output to papers.validated / papers.review / papers.failed
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, field_validator, model_validator
import uuid


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Enums ──────────────────────────────────────────────────────────────────

class RoutingDecision(str, Enum):
    AUTO_SAVE        = "AUTO_SAVE"
    REVIEW_REQUIRED  = "REVIEW_REQUIRED"
    BLOCK            = "BLOCK"


class PipelineStatus(str, Enum):
    extracted = "extracted"
    failed    = "failed"
    blocked   = "blocked"


class FacultyStatus(str, Enum):
    active    = "active"
    inactive  = "inactive"
    not_found = "not_found"


class ExtractionTier(str, Enum):
    REGEX    = "REGEX"
    CROSSREF = "CROSSREF"
    NLP      = "NLP"
    LLM      = "LLM"


# ── Module 2 Input Schema (IngestedPayload) ───────────────────────────────

class IngestedAttachment(BaseModel):
    filename: str
    content_type: str
    size_bytes: int
    checksum_sha256: str    # SHA256 of raw file bytes
    s3_key: str
    s3_bucket: str

    @property
    def storage_uri(self) -> str:
        return f"s3://{self.s3_bucket}/{self.s3_key}"


class IngestedEmail(BaseModel):
    message_id: str
    subject: str
    sender: str
    recipients: List[str]
    received_at: datetime
    idempotency_key: str


class IngestedContent(BaseModel):
    raw_text: str = Field(..., min_length=50)
    raw_text_hash: str
    attachments: List[IngestedAttachment] = []


class IngestedSecurity(BaseModel):
    pii_redacted: bool
    source_domain_verified: bool
    clamav_scanned: bool
    clamav_result: str


class IngestedPayload(BaseModel):
    """Strict schema for consuming from ingest.raw (Module 2 output)."""
    event_id: str
    contract_version: str
    pipeline_status: str
    created_at: datetime
    email: IngestedEmail
    content: IngestedContent
    security: IngestedSecurity

    @field_validator("contract_version")
    @classmethod
    def must_be_v1(cls, v: str) -> str:
        if v != "v1":
            raise ValueError(f"Expected contract_version='v1', got '{v}'")
        return v

    @field_validator("pipeline_status")
    @classmethod
    def must_be_ingested(cls, v: str) -> str:
        if v != "ingested":
            raise ValueError(f"Expected pipeline_status='ingested', got '{v}'")
        return v


# ── Extraction Metadata ───────────────────────────────────────────────────

class ExtractedAuthors(BaseModel):
    names: List[str] = []
    confidence: float = Field(0.0, ge=0.0, le=1.0)


class ExtractedMetadata(BaseModel):
    title: Optional[str]            = None
    authors: ExtractedAuthors       = Field(default_factory=ExtractedAuthors)
    doi: Optional[str]              = None
    year: Optional[int]             = None
    venue: Optional[str]            = None
    abstract: Optional[str]         = None
    keywords: List[str]             = []
    title_confidence: float         = Field(0.0, ge=0.0, le=1.0)
    venue_year_confidence: float    = Field(0.0, ge=0.0, le=1.0)
    extraction_tier: ExtractionTier = ExtractionTier.REGEX
    crossref_verified: bool         = False
    # Module 4 requires one of journal/conference/thesis/book_chapter/unknown.
    # None of the 4 extraction tiers actually classify paper type today —
    # this defaults to "unknown" rather than guessing. Proper classification
    # (e.g. venue-name heuristics or a 5th cascade tier) is a real feature
    # gap, not something a compatibility fix should silently paper over.
    paper_type: str                 = "unknown"


class OverallConfidence(BaseModel):
    score: float = Field(..., ge=0.0, le=1.0)
    authors_score: float    = Field(..., ge=0.0, le=1.0)
    title_score: float      = Field(..., ge=0.0, le=1.0)
    venue_year_score: float = Field(..., ge=0.0, le=1.0)
    formula: str = "(authors*0.40) + (title*0.30) + (venue_year*0.30)"

    @model_validator(mode="after")
    def validate_score_formula(self) -> "OverallConfidence":
        expected = (
            self.authors_score * 0.40
            + self.title_score * 0.30
            + self.venue_year_score * 0.30
        )
        if abs(self.score - expected) > 0.001:
            raise ValueError(
                f"overall_confidence.score {self.score} does not match formula result {expected:.4f}"
            )
        return self


# ── Faculty / Directory Context ───────────────────────────────────────────

class EnrichedContext(BaseModel):
    faculty_id: str
    faculty_name: Optional[str]     = None
    faculty_email: Optional[str]    = None
    department_code: Optional[str]  = None
    faculty_status: FacultyStatus   = FacultyStatus.not_found


# ── Validation Issues ─────────────────────────────────────────────────────

class ValidationIssue(BaseModel):
    code: str
    message: str
    action: str   # "BLOCK" | "REVIEW_REQUIRED" | "WARN"


# ── Routing Decision Block ────────────────────────────────────────────────

class RoutingDecisionBlock(BaseModel):
    final_action: RoutingDecision
    reasons: List[str]
    target_topic: str
    confidence_threshold_used: float
    overall_confidence: float


# ── Output Schema: paper.extracted.v1 ────────────────────────────────────
#
# Wire shape matches Module 4's KafkaPayload contract EXACTLY — see
# module4-storage/app/models/schemas.py::KafkaPayload's docstring, which
# documents the real confirmed message shape Module 4's consumer parses
# (and module4-storage/app/consumer.py::_build_paper_create, which reads
# specific nested paths out of it). Module 4 is locked, so this side had
# to move to match it. The old version of this model didn't nest
# anything the way Module 4 expects: pipeline_status/created_at were
# top-level instead of pipeline_status living inside routing_decision;
# there was no extraction_id, no content_reference, no audit_trail; the
# field was "source_event_id" where Module 4 expects nothing; and
# authors were {names: [str]} instead of Module 4's {name, affiliation,
# order} per author.
class PaperExtractedV1(BaseModel):
    extraction_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_id: str       = Field(default_factory=lambda: str(uuid.uuid4()))
    idempotency_key: str
    contract_version: str = "v1"

    routing_decision: Dict[str, Any]
    extraction_result: Dict[str, Any]
    enriched_context: Dict[str, Any]
    content_reference: Dict[str, Any]
    validation_issues: List[Dict[str, Any]] = Field(default_factory=list)
    audit_trail: Dict[str, Any] = Field(default_factory=dict)
    processing: Dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "allow"}

    def to_kafka_payload(self) -> bytes:
        return self.model_dump_json(indent=None).encode("utf-8")

    @classmethod
    def build(
        cls,
        *,
        idempotency_key: str,
        extracted_metadata: "ExtractedMetadata",
        overall_confidence: "OverallConfidence",
        enriched_context: "EnrichedContext",
        routing: "RoutingDecisionBlock",
        validation_issues: List["ValidationIssue"],
        pipeline_status: "PipelineStatus",
        raw_text_hash: str,
        attachments: List["IngestedAttachment"],
        worker_id: str,
        source_event_id: Optional[str] = None,
    ) -> "PaperExtractedV1":
        # W3C traceparent format: "00-<32 hex trace id>-<16 hex span id>-01"
        # (Module 4's KafkaPayload.trace_id_uuid parses exactly this shape)
        trace_id_hex = uuid.uuid4().hex
        span_id_hex = uuid.uuid4().hex[:16]

        raw_names = extracted_metadata.authors.names
        authors = [
            {"name": name, "affiliation": None, "order": i + 1}
            for i, name in enumerate(raw_names)
        ] or [{"name": "Unknown Author", "affiliation": None, "order": 1}]

        return cls(
            idempotency_key=idempotency_key,
            routing_decision={
                "pipeline_status": pipeline_status.value,
                "final_action": routing.final_action.value,
                "overall_confidence": overall_confidence.score,
                "applied_threshold": routing.confidence_threshold_used,
                "decision_rationale": {"reasons": routing.reasons},
            },
            extraction_result={
                "metadata": {
                    "title": extracted_metadata.title,
                    "authors": authors,
                    "doi": extracted_metadata.doi,
                    "year": extracted_metadata.year,
                    "venue": extracted_metadata.venue,
                    "journal": extracted_metadata.venue,
                    "abstract": extracted_metadata.abstract,
                    "keywords": extracted_metadata.keywords,
                    "paper_type": extracted_metadata.paper_type,
                },
                "confidence_scores": {
                    "overall": overall_confidence.score,
                    "authors": overall_confidence.authors_score,
                    "title": overall_confidence.title_score,
                    "venue_year": overall_confidence.venue_year_score,
                    "formula": overall_confidence.formula,
                },
                "extraction_source": extracted_metadata.extraction_tier.value,
                "embedding": None,  # Module 4 generates this locally, per its locked design
            },
            enriched_context={
                "faculty_id": enriched_context.faculty_id,
                "faculty_name": enriched_context.faculty_name,
                "faculty_email": enriched_context.faculty_email,
                "department_code": enriched_context.department_code,
                "faculty_status": enriched_context.faculty_status.value,
            },
            content_reference={
                "raw_text_hash": raw_text_hash,
                "attachments": [
                    {
                        "filename": a.filename,
                        "uri": a.storage_uri,
                        "checksum_sha256": a.checksum_sha256,
                    }
                    for a in attachments
                ],
            },
            validation_issues=[
                {
                    "code": i.code,
                    "message": i.message,
                    "action": i.action,
                    "source": "module3",
                }
                for i in validation_issues
            ],
            audit_trail={
                "trace_id": f"00-{trace_id_hex}-{span_id_hex}-01",
                "span_id": span_id_hex,
                "processing_times": {},
            },
            processing={"worker_id": worker_id, "source_event_id": source_event_id},
        )
