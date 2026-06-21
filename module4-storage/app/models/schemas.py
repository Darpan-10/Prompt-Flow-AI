"""
Module 4 – Pydantic V2 Schemas
Exact column definitions from spec. No placeholders.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


# ── Sub-models ────────────────────────────────────────────────────────────────

class Author(BaseModel):
    name: str = Field(..., min_length=1, max_length=300)
    affiliation: Optional[str] = None
    order: int = Field(..., ge=1)

    model_config = {"from_attributes": True}


class Attachment(BaseModel):
    filename: str = Field(..., min_length=1)
    uri: str = Field(..., min_length=1)
    checksum_sha256: str = Field(..., pattern=r"^[a-f0-9]{64}$")

    model_config = {"from_attributes": True}


# ── Paper schemas ─────────────────────────────────────────────────────────────

class PaperCreate(BaseModel):
    ingestion_idempotency_key: str = Field(..., min_length=8, max_length=64)
    extraction_id: UUID

    title: str = Field(..., min_length=10)
    authors: List[Author] = Field(..., min_length=1)
    venue: Optional[str] = Field(None, max_length=500)
    year: int = Field(..., ge=2000)
    doi: Optional[str] = Field(None, max_length=200)
    paper_type: Literal["journal", "conference", "thesis", "book_chapter", "unknown"]

    faculty_id: UUID
    faculty_email: str = Field(..., max_length=200)
    department_code: str = Field(..., max_length=20)

    status: Literal["PUBLISHED", "DRAFT", "REJECTED", "PENDING_REVIEW"]
    overall_confidence: float = Field(..., ge=0.0, le=1.0)

    raw_text_hash: str = Field(..., pattern=r"^[a-f0-9]{64}$")
    attachment_uris: List[Attachment] = Field(default_factory=list)
    embedding: Optional[List[float]] = Field(None)

    @field_validator("embedding")
    @classmethod
    def validate_embedding(cls, v: Optional[List[float]]) -> Optional[List[float]]:
        if v is not None and len(v) != 768:
            raise ValueError(f"Embedding must be 768-dimensional, got {len(v)}")
        return v

    @field_validator("year")
    @classmethod
    def validate_year(cls, v: int) -> int:
        import datetime
        max_year = datetime.datetime.now().year + 1
        if v > max_year:
            raise ValueError(f"Year {v} is in the future (max allowed: {max_year})")
        return v

    model_config = {"from_attributes": True}


class PaperRead(BaseModel):
    paper_id: UUID
    ingestion_idempotency_key: str
    extraction_id: UUID
    title: str
    authors: List[Author]
    venue: Optional[str]
    year: int
    doi: Optional[str]
    paper_type: str
    faculty_id: UUID
    faculty_email: str
    department_code: str
    status: str
    overall_confidence: float
    raw_text_hash: str
    attachment_uris: List[Attachment]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PaperUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=10)
    venue: Optional[str] = Field(None, max_length=500)
    year: Optional[int] = Field(None, ge=2000)
    doi: Optional[str] = Field(None, max_length=200)
    paper_type: Optional[Literal["journal", "conference", "thesis", "book_chapter", "unknown"]] = None
    status: Optional[Literal["PUBLISHED", "DRAFT", "REJECTED", "PENDING_REVIEW"]] = None
    overall_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    embedding: Optional[List[float]] = None
    change_reason: str = Field(..., min_length=5, max_length=500)

    @field_validator("embedding")
    @classmethod
    def validate_embedding(cls, v: Optional[List[float]]) -> Optional[List[float]]:
        if v is not None and len(v) != 768:
            raise ValueError(f"Embedding must be 768-dimensional, got {len(v)}")
        return v


# ── PaperVersion schemas ──────────────────────────────────────────────────────

class PaperVersionRead(BaseModel):
    version_id: UUID
    paper_id: UUID
    version_number: int
    changed_by: str
    changed_at: datetime
    change_reason: Optional[str]
    before_state: Optional[Dict[str, Any]]
    after_state: Dict[str, Any]

    model_config = {"from_attributes": True}


# ── ValidationIssue schemas ───────────────────────────────────────────────────

class ValidationIssueCreate(BaseModel):
    paper_id: UUID
    issue_code: str = Field(..., max_length=50)
    severity: Literal["error", "warning", "info"]
    action: Literal["AUTO_SAVE", "REVIEW_REQUIRED", "BLOCK"]
    json_path: Optional[str] = Field(None, max_length=200)
    extracted_value: Optional[str] = None
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    threshold: Optional[float] = Field(None, ge=0.0, le=1.0)
    source: str = Field(..., max_length=50)
    message: str

    model_config = {"from_attributes": True}


class ValidationIssueRead(BaseModel):
    issue_id: UUID
    paper_id: UUID
    issue_code: str
    severity: str
    action: str
    json_path: Optional[str]
    extracted_value: Optional[str]
    confidence: Optional[float]
    threshold: Optional[float]
    source: str
    message: str
    resolved_at: Optional[datetime]
    resolved_by: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class ValidationIssueResolve(BaseModel):
    resolved_by: str = Field(..., min_length=1, max_length=100)


# ── Search schemas ────────────────────────────────────────────────────────────

class FullTextSearchRequest(BaseModel):
    query: str = Field(..., min_length=2, max_length=500)
    department_code: Optional[str] = None
    year: Optional[int] = None
    status: Optional[str] = "PUBLISHED"
    limit: int = Field(20, ge=1, le=50)
    offset: int = Field(0, ge=0)


class FullTextSearchResult(BaseModel):
    paper_id: UUID
    title: str
    venue: Optional[str]
    year: int
    doi: Optional[str]
    faculty_email: str
    department_code: str
    status: str
    overall_confidence: float
    rank: float

    model_config = {"from_attributes": True}


class SemanticSearchRequest(BaseModel):
    embedding: List[float] = Field(..., min_length=768, max_length=768)
    department_code: Optional[str] = None
    limit: int = Field(10, ge=1, le=50)
    similarity_threshold: float = Field(0.7, ge=0.0, le=1.0)


class SemanticSearchResult(BaseModel):
    paper_id: UUID
    title: str
    venue: Optional[str]
    year: int
    doi: Optional[str]
    faculty_email: str
    department_code: str
    status: str
    overall_confidence: float
    similarity: float

    model_config = {"from_attributes": True}


# ── Health / generic schemas ──────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str
    service: str = "module4-storage"
    database: bool
    kafka: bool
    redis: bool


class PaginatedResponse(BaseModel):
    items: List[Any]
    total: int
    limit: int
    offset: int


class KafkaPayload(BaseModel):
    """
    Represents the REAL paper.extracted.v1 payload from Module 3.

    Confirmed against an actual sample message from papers.validated
    on 2026-06-19. Top-level shape:

    {
      "extraction_id": "...",
      "event_id": "...",
      "idempotency_key": "...",          <- TOP-LEVEL, not nested under email
      "contract_version": "v1",
      "routing_decision": {
          "pipeline_status": "validated",
          "final_action": "AUTO_SAVE",    <- "final_action", not "action"
          "overall_confidence": 0.94,
          "applied_threshold": 0.85,
          "decision_rationale": {...}
      },
      "extraction_result": {
          "metadata": {...},
          "confidence_scores": {...},
          "extraction_source": "CROSSREF_API",
          "embedding": null                <- always null from Module 3
      },
      "enriched_context": {
          "faculty_id": "...",
          "faculty_name": "...",
          "faculty_email": "...",
          "department_code": "...",
          "faculty_status": "active"
      },
      "content_reference": {
          "raw_text_hash": "...",
          "attachments": [...]
      },
      "validation_issues": [...],
      "audit_trail": {
          "trace_id": "00-1234...-abcdef...-01",   <- W3C traceparent format
          "span_id": "...",
          "processing_times": {...}
      },
      "processing": {
          "attempts": [...]
      }
    }

    Note: there is NO "email" key and NO top-level "created_at" or
    "pipeline_status" in the real payload -- pipeline_status lives
    under routing_decision.
    """

    extraction_id: str
    event_id: str
    idempotency_key: str
    contract_version: str

    routing_decision: Dict[str, Any]
    extraction_result: Dict[str, Any]
    enriched_context: Dict[str, Any]
    content_reference: Dict[str, Any]
    validation_issues: List[Dict[str, Any]] = Field(default_factory=list)

    audit_trail: Optional[Dict[str, Any]] = None
    processing: Optional[Dict[str, Any]] = None

    model_config = {"extra": "allow"}  # tolerate future additive fields

    # -- Convenience accessors ----------------------------------------------

    @property
    def department_code(self) -> str:
        return self.enriched_context.get("department_code", "UNKNOWN")

    @property
    def faculty_id(self) -> str:
        return self.enriched_context.get("faculty_id", "00000000-0000-0000-0000-000000000000")

    @property
    def faculty_email(self) -> str:
        return self.enriched_context.get("faculty_email", "unknown@srmap.edu.in")

    @property
    def final_action(self) -> str:
        return self.routing_decision.get("final_action", "REVIEW_REQUIRED")

    @property
    def overall_confidence(self) -> float:
        return float(self.routing_decision.get("overall_confidence", 0.0))

    @property
    def trace_id_uuid(self) -> Optional[str]:
        """
        audit_trail.trace_id is W3C traceparent format:
        "00-<32 hex trace id>-<16 hex span id>-01"
        Extract the 32-hex segment and format as a UUID for Postgres,
        since audit_log.trace_id column is UUID type.
        """
        if not self.audit_trail:
            return None
        raw = self.audit_trail.get("trace_id", "")
        parts = raw.split("-")
        if len(parts) >= 2 and len(parts[1]) == 32:
            hex32 = parts[1]
            return f"{hex32[0:8]}-{hex32[8:12]}-{hex32[12:16]}-{hex32[16:20]}-{hex32[20:32]}"
        return None

    @property
    def resolved_status(self) -> str:
        mapping = {
            "AUTO_SAVE": "PUBLISHED",
            "REVIEW_REQUIRED": "PENDING_REVIEW",
            "BLOCK": "REJECTED",
        }
        return mapping.get(self.final_action, "DRAFT")
