"""
paper.ingested.v1 — Strict Pydantic v2 event contract.

HARD RULES:
- contract_version = "v1"
- pipeline_status = "ingested"
- timestamps = ISO8601
- received_at <= now + 5 minutes
- content.raw_text >= 50 chars
- processing.attempts = []
- security.pii_redacted = True
- security.source_domain_verified = True
"""

from pydantic import BaseModel, field_validator, model_validator, Field
from typing import List, Optional
from datetime import datetime, timezone, timedelta
import uuid


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EmailMetadata(BaseModel):
    message_id: str = Field(..., description="RFC2822 Message-ID header")
    thread_id: Optional[str] = None
    subject: str
    sender: str
    recipients: List[str]
    received_at: datetime
    idempotency_key: str = Field(..., description="sha256(message_id:filename)")

    @field_validator("sender")
    @classmethod
    def sender_must_be_srmap(cls, v: str) -> str:
        if "@srmap.edu.in" not in v.lower():
            raise ValueError(f"sender domain must be @srmap.edu.in, got: {v}")
        return v

    @field_validator("received_at")
    @classmethod
    def received_at_not_future(cls, v: datetime) -> datetime:
        now = _utcnow()
        if v > now + timedelta(minutes=5):
            raise ValueError(
                f"received_at ({v.isoformat()}) is more than 5 minutes in the future"
            )
        return v


class AttachmentInfo(BaseModel):
    filename: str
    content_type: str
    size_bytes: int
    checksum_sha256: str = Field(..., description="SHA256 of raw file bytes — NEVER equals raw_text_hash")
    s3_key: str
    s3_bucket: str


class ContentBlock(BaseModel):
    raw_text: str = Field(..., min_length=50, description="PII-redacted email body text")
    raw_text_hash: str = Field(..., description="SHA256 of PII-redacted text — NEVER equals checksum_sha256")
    language: str = "en"
    attachments: List[AttachmentInfo] = []

    @field_validator("raw_text")
    @classmethod
    def raw_text_min_length(cls, v: str) -> str:
        stripped = v.strip()
        if len(stripped) < 50:
            raise ValueError(
                f"raw_text must be >= 50 characters after stripping, got {len(stripped)}"
            )
        return stripped


class ProcessingBlock(BaseModel):
    attempts: List[dict] = Field(default_factory=list)
    worker_id: str
    processed_at: datetime = Field(default_factory=_utcnow)

    @field_validator("attempts")
    @classmethod
    def attempts_must_be_empty_on_ingestion(cls, v: list) -> list:
        if v:
            raise ValueError("processing.attempts must be [] at ingestion stage")
        return v


class SecurityBlock(BaseModel):
    pii_redacted: bool = True
    source_domain_verified: bool = True
    clamav_scanned: bool = True
    clamav_result: str = "CLEAN"

    @field_validator("pii_redacted")
    @classmethod
    def pii_must_be_redacted(cls, v: bool) -> bool:
        if not v:
            raise ValueError("security.pii_redacted must be True — PII must be redacted before ingestion")
        return v

    @field_validator("source_domain_verified")
    @classmethod
    def domain_must_be_verified(cls, v: bool) -> bool:
        if not v:
            raise ValueError("security.source_domain_verified must be True")
        return v


class PaperIngestedV1(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    contract_version: str = "v1"
    pipeline_status: str = "ingested"
    created_at: datetime = Field(default_factory=_utcnow)

    email: EmailMetadata
    content: ContentBlock
    processing: ProcessingBlock
    security: SecurityBlock

    @field_validator("contract_version")
    @classmethod
    def must_be_v1(cls, v: str) -> str:
        if v != "v1":
            raise ValueError(f"contract_version must be 'v1', got '{v}'")
        return v

    @field_validator("pipeline_status")
    @classmethod
    def must_be_ingested(cls, v: str) -> str:
        if v != "ingested":
            raise ValueError(f"pipeline_status must be 'ingested', got '{v}'")
        return v

    @model_validator(mode="after")
    def hashes_must_never_match(self) -> "PaperIngestedV1":
        for att in self.content.attachments:
            if att.checksum_sha256 == self.content.raw_text_hash:
                raise ValueError(
                    "CRITICAL: checksum_sha256 and raw_text_hash must NEVER match. "
                    "They hash different data (file bytes vs redacted text)."
                )
        return self

    def to_kafka_payload(self) -> bytes:
        """Serialize to JSON bytes for Kafka."""
        return self.model_dump_json(indent=None).encode("utf-8")
