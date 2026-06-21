"""
Module 4 – ORM Models
SQLAlchemy 2.0 mapped dataclasses matching the locked schema exactly.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

try:
    from pgvector.sqlalchemy import Vector
    _PGVECTOR_AVAILABLE = True
except ImportError:
    _PGVECTOR_AVAILABLE = False
    Vector = None


class Base(DeclarativeBase):
    pass


# ── papers (partitioned by created_at YEAR) ───────────────────────────────────

class Paper(Base):
    __tablename__ = "papers"

    paper_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    ingestion_idempotency_key: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True
    )
    extraction_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    # Core metadata
    title: Mapped[str] = mapped_column(Text, nullable=False)
    authors: Mapped[dict] = mapped_column(JSONB, nullable=False)
    venue: Mapped[Optional[str]] = mapped_column(String(500))
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    doi: Mapped[Optional[str]] = mapped_column(String(200))
    paper_type: Mapped[str] = mapped_column(String(50), nullable=False)

    # Faculty & department
    faculty_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    faculty_email: Mapped[str] = mapped_column(String(200), nullable=False)
    department_code: Mapped[str] = mapped_column(String(20), nullable=False)

    # Status & confidence
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    overall_confidence: Mapped[Decimal] = mapped_column(
        Numeric(3, 2), nullable=False
    )

    # Content references
    raw_text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    attachment_uris: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    # Vector embedding (populated when status='PUBLISHED')
    if _PGVECTOR_AVAILABLE:
        embedding: Mapped[Optional[list[float]]] = mapped_column(Vector(768))
    else:
        embedding: Mapped[Optional[Any]] = mapped_column(JSONB)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    versions: Mapped[list[PaperVersion]] = relationship(
        "PaperVersion", back_populates="paper", lazy="select"
    )
    validation_issues: Mapped[list[ValidationIssue]] = relationship(
        "ValidationIssue", back_populates="paper", lazy="select"
    )

    __table_args__ = (
        CheckConstraint("year >= 2000 AND year <= EXTRACT(YEAR FROM NOW()) + 1", name="chk_papers_year"),
        CheckConstraint("paper_type IN ('journal','conference','thesis','book_chapter','unknown')", name="chk_papers_type"),
        CheckConstraint("status IN ('PUBLISHED','DRAFT','REJECTED','PENDING_REVIEW')", name="chk_papers_status"),
        CheckConstraint("overall_confidence >= 0.0 AND overall_confidence <= 1.0", name="chk_papers_confidence"),
        UniqueConstraint("doi", "department_code", name="papers_doi_unique"),
        # Indexes declared here (also created explicitly in migration)
        Index("idx_papers_doi", "doi", postgresql_where="doi IS NOT NULL"),
        Index("idx_papers_dept_status", "department_code", "status"),
        Index("idx_papers_faculty", "faculty_id"),
        Index("idx_papers_year", "year"),
        Index("idx_papers_idempotency", "ingestion_idempotency_key"),
        Index(
            "idx_papers_dashboard",
            "department_code", "status", "created_at",
            postgresql_include=["title", "overall_confidence", "faculty_email"],
        ),
        # Partitioned by created_at year
        {"postgresql_partition_by": "RANGE (created_at)"},
    )

    def __repr__(self) -> str:
        return f"<Paper {self.paper_id} title={self.title[:40]!r} status={self.status}>"


# ── paper_versions (partitioned by changed_at YEAR) ──────────────────────────

class PaperVersion(Base):
    __tablename__ = "paper_versions"

    version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    paper_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("papers.paper_id", ondelete="CASCADE"),
        nullable=False,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)

    # Change metadata
    changed_by: Mapped[str] = mapped_column(String(100), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    change_reason: Mapped[Optional[str]] = mapped_column(String(500))

    # State snapshots
    before_state: Mapped[Optional[dict]] = mapped_column(JSONB)
    after_state: Mapped[dict] = mapped_column(JSONB, nullable=False)

    # Relationship
    paper: Mapped[Paper] = relationship("Paper", back_populates="versions")

    __table_args__ = (
        UniqueConstraint("paper_id", "version_number", name="paper_versions_unique_version"),
        Index("idx_paper_versions_paper_id", "paper_id"),
        {"postgresql_partition_by": "RANGE (changed_at)"},
    )

    def __repr__(self) -> str:
        return f"<PaperVersion paper={self.paper_id} v={self.version_number}>"


# ── validation_issues ─────────────────────────────────────────────────────────

class ValidationIssue(Base):
    __tablename__ = "validation_issues"

    issue_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    paper_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("papers.paper_id", ondelete="CASCADE"),
        nullable=False,
    )

    issue_code: Mapped[str] = mapped_column(String(50), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    action: Mapped[str] = mapped_column(String(20), nullable=False)

    json_path: Mapped[Optional[str]] = mapped_column(String(200))
    extracted_value: Mapped[Optional[str]] = mapped_column(Text)
    confidence: Mapped[Optional[Decimal]] = mapped_column(Numeric(4, 3))
    threshold: Mapped[Optional[Decimal]] = mapped_column(Numeric(4, 3))
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)

    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[Optional[str]] = mapped_column(String(100))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationship
    paper: Mapped[Paper] = relationship("Paper", back_populates="validation_issues")

    __table_args__ = (
        CheckConstraint("severity IN ('error','warning','info')", name="chk_issue_severity"),
        CheckConstraint("action IN ('AUTO_SAVE','REVIEW_REQUIRED','BLOCK')", name="chk_issue_action"),
        Index("idx_validation_issues_paper_id", "paper_id"),
        Index("idx_validation_issues_severity", "severity"),
    )

    def __repr__(self) -> str:
        return f"<ValidationIssue {self.issue_code} [{self.severity}] paper={self.paper_id}>"


# ── audit_log (partitioned by logged_at MONTH, IMMUTABLE) ────────────────────

class AuditLog(Base):
    __tablename__ = "audit_log"

    log_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    logged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    action: Mapped[str] = mapped_column(String(100), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(50), nullable=False)
    actor_id: Mapped[Optional[str]] = mapped_column(String(200))
    resource_type: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_id: Mapped[Optional[str]] = mapped_column(String(200))

    details: Mapped[Optional[dict]] = mapped_column(JSONB)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45))  # IPv6 max
    trace_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))

    __table_args__ = (
        Index("idx_audit_log_time", "logged_at", postgresql_using="brin"),
        Index("idx_audit_log_resource", "resource_type", "resource_id"),
        Index("idx_audit_log_actor", "actor_id"),
        {"postgresql_partition_by": "RANGE (logged_at)"},
    )

    def __repr__(self) -> str:
        return f"<AuditLog {self.action} resource={self.resource_id} at={self.logged_at}>"
