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

class PaperExtractedV1(BaseModel):
    event_id: str               = Field(default_factory=lambda: str(uuid.uuid4()))
    contract_version: str       = "v1"
    pipeline_status: PipelineStatus
    created_at: datetime        = Field(default_factory=_utcnow)

    # Pass-through from Module 2
    source_event_id: str
    idempotency_key: str

    # Extraction results
    extracted_metadata: ExtractedMetadata
    overall_confidence: OverallConfidence
    enriched_context: EnrichedContext
    validation_issues: List[ValidationIssue] = []

    # Deterministic routing
    routing_decision: RoutingDecisionBlock

    def to_kafka_payload(self) -> bytes:
        return self.model_dump_json(indent=None).encode("utf-8")
