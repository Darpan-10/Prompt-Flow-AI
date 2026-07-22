"""
Module 4 – Repository Layer
All async, SQLAlchemy 2.0 style. RLS enforced by session context.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional
from uuid import UUID

from sqlalchemy import select, update, text, func, and_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.orm import Paper, PaperVersion, ValidationIssue, AuditLog
from app.models.schemas import (
    PaperCreate,
    PaperRead,
    PaperUpdate,
    PaperVersionRead,
    ValidationIssueCreate,
    ValidationIssueRead,
    FullTextSearchResult,
    SemanticSearchResult,
)


# ── Paper Repository ──────────────────────────────────────────────────────────

class PaperRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, data: PaperCreate) -> PaperRead:
        """
        Insert a new paper row.
        The trg_paper_initial_version trigger automatically creates version 1.
        The trg_paper_audit trigger writes to audit_log.
        """
        paper = Paper(
            ingestion_idempotency_key=data.ingestion_idempotency_key,
            extraction_id=data.extraction_id,
            title=data.title,
            authors=[a.model_dump() for a in data.authors],
            venue=data.venue,
            year=data.year,
            doi=data.doi,
            paper_type=data.paper_type,
            faculty_id=data.faculty_id,
            faculty_email=data.faculty_email,
            department_code=data.department_code,
            status=data.status,
            overall_confidence=data.overall_confidence,
            raw_text_hash=data.raw_text_hash,
            attachment_uris=[a.model_dump() for a in data.attachment_uris],
            embedding=data.embedding,
        )
        self.session.add(paper)
        await self.session.flush()
        await self.session.refresh(paper)
        return PaperRead.model_validate(paper)

    async def get_by_id(self, paper_id: UUID) -> Optional[PaperRead]:
        """Fetch single paper. RLS filters automatically."""
        result = await self.session.execute(
            select(Paper).where(Paper.paper_id == paper_id)
        )
        row = result.scalar_one_or_none()
        return PaperRead.model_validate(row) if row else None

    async def get_by_idempotency_key(self, key: str) -> Optional[PaperRead]:
        """Idempotency check — returns existing paper if already processed."""
        result = await self.session.execute(
            select(Paper).where(Paper.ingestion_idempotency_key == key)
        )
        row = result.scalar_one_or_none()
        return PaperRead.model_validate(row) if row else None

    async def update(self, paper_id: UUID, data: PaperUpdate) -> Optional[PaperRead]:
        """
        Update paper columns.
        trg_paper_versioning fires and inserts into paper_versions automatically.
        change_reason is passed via set_config('app.change_reason', ...).
        """
        # Inject change_reason for the trigger to pick up.
        # CRITICAL FIX: was `SET LOCAL app.change_reason = :r`, which
        # raises a PostgreSQL syntax error ("syntax error at or near
        # '$1'") -- SET is a utility statement and does not accept
        # protocol-level bind parameters for the value being set. Found
        # via real-PostgreSQL integration testing while building
        # Module 6 (same RLS context pattern); see app/database.py's
        # set_rls_context() docstring for the full explanation.
        # set_config() is a regular function call, so bind parameters
        # work normally here.
        await self.session.execute(
            text("SELECT set_config('app.change_reason', :r, true)"),
            {"r": data.change_reason},
        )

        update_fields = data.model_dump(
            exclude={"change_reason"},
            exclude_none=True,
        )
        update_fields["updated_at"] = datetime.utcnow()

        result = await self.session.execute(
            update(Paper)
            .where(Paper.paper_id == paper_id)
            .values(**update_fields)
            .returning(Paper)
        )
        row = result.scalar_one_or_none()
        return PaperRead.model_validate(row) if row else None

    async def list(
        self,
        *,
        department_code: Optional[str] = None,
        status: Optional[str] = None,
        year: Optional[int] = None,
        faculty_id: Optional[UUID] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[PaperRead]:
        """List papers. RLS restricts rows by session department automatically."""
        q = select(Paper)
        if department_code:
            q = q.where(Paper.department_code == department_code)
        if status:
            q = q.where(Paper.status == status)
        if year:
            q = q.where(Paper.year == year)
        if faculty_id:
            q = q.where(Paper.faculty_id == faculty_id)
        q = q.order_by(Paper.created_at.desc()).limit(limit).offset(offset)

        result = await self.session.execute(q)
        return [PaperRead.model_validate(r) for r in result.scalars().all()]

    async def count(
        self,
        *,
        department_code: Optional[str] = None,
        status: Optional[str] = None,
    ) -> int:
        q = select(func.count()).select_from(Paper)
        if department_code:
            q = q.where(Paper.department_code == department_code)
        if status:
            q = q.where(Paper.status == status)
        result = await self.session.execute(q)
        return result.scalar_one()

    async def upsert_on_conflict_ignore(self, data: PaperCreate) -> tuple[PaperRead, bool]:
        """
        Try to insert; if idempotency_key already exists, return existing row.
        Returns (paper, created: bool).
        """
        existing = await self.get_by_idempotency_key(data.ingestion_idempotency_key)
        if existing:
            return existing, False
        paper = await self.create(data)
        return paper, True


# ── PaperVersion Repository ───────────────────────────────────────────────────

class PaperVersionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_versions(self, paper_id: UUID) -> List[PaperVersionRead]:
        result = await self.session.execute(
            select(PaperVersion)
            .where(PaperVersion.paper_id == paper_id)
            .order_by(PaperVersion.version_number.asc())
        )
        return [PaperVersionRead.model_validate(r) for r in result.scalars().all()]

    async def get_version(self, paper_id: UUID, version_number: int) -> Optional[PaperVersionRead]:
        result = await self.session.execute(
            select(PaperVersion).where(
                and_(
                    PaperVersion.paper_id == paper_id,
                    PaperVersion.version_number == version_number,
                )
            )
        )
        row = result.scalar_one_or_none()
        return PaperVersionRead.model_validate(row) if row else None

    async def latest_version_number(self, paper_id: UUID) -> int:
        result = await self.session.execute(
            select(func.max(PaperVersion.version_number)).where(
                PaperVersion.paper_id == paper_id
            )
        )
        return result.scalar_one_or_none() or 0


# ── ValidationIssue Repository ────────────────────────────────────────────────

class ValidationIssueRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, data: ValidationIssueCreate) -> ValidationIssueRead:
        issue = ValidationIssue(**data.model_dump())
        self.session.add(issue)
        await self.session.flush()
        await self.session.refresh(issue)
        return ValidationIssueRead.model_validate(issue)

    async def bulk_create(self, items: List[ValidationIssueCreate]) -> List[ValidationIssueRead]:
        created = []
        for item in items:
            created.append(await self.create(item))
        return created

    async def list_for_paper(self, paper_id: UUID) -> List[ValidationIssueRead]:
        result = await self.session.execute(
            select(ValidationIssue)
            .where(ValidationIssue.paper_id == paper_id)
            .order_by(ValidationIssue.created_at.asc())
        )
        return [ValidationIssueRead.model_validate(r) for r in result.scalars().all()]

    async def resolve(self, issue_id: UUID, resolved_by: str) -> Optional[ValidationIssueRead]:
        result = await self.session.execute(
            update(ValidationIssue)
            .where(ValidationIssue.issue_id == issue_id)
            .values(resolved_at=datetime.utcnow(), resolved_by=resolved_by)
            .returning(ValidationIssue)
        )
        row = result.scalar_one_or_none()
        return ValidationIssueRead.model_validate(row) if row else None


# ── Search Repository ─────────────────────────────────────────────────────────

class SearchRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def fulltext_search(
        self,
        query: str,
        *,
        department_code: Optional[str] = None,
        year: Optional[int] = None,
        status: str = "PUBLISHED",
        limit: int = 20,
        offset: int = 0,
    ) -> List[FullTextSearchResult]:
        """
        Full-text search using GIN index on tsvector(title || venue).
        Uses ts_rank_cd for relevance scoring.
        """
        params: dict = {"query": query, "status": status, "limit": limit, "offset": offset}

        dept_filter = "AND p.department_code = :dept" if department_code else ""
        year_filter = "AND p.year = :year" if year else ""
        if department_code:
            params["dept"] = department_code
        if year:
            params["year"] = year

        sql = text(f"""
            SELECT
                p.paper_id,
                p.title,
                p.venue,
                p.year,
                p.doi,
                p.faculty_email,
                p.department_code,
                p.status,
                CAST(p.overall_confidence AS FLOAT) AS overall_confidence,
                ts_rank_cd(
                    setweight(to_tsvector('english', coalesce(p.title, '')), 'A') ||
                    setweight(to_tsvector('english', coalesce(p.venue,  '')), 'B'),
                    plainto_tsquery('english', :query)
                ) AS rank
            FROM papers p
            WHERE
                p.status = :status
                {dept_filter}
                {year_filter}
                AND (
                    setweight(to_tsvector('english', coalesce(p.title, '')), 'A') ||
                    setweight(to_tsvector('english', coalesce(p.venue,  '')), 'B')
                ) @@ plainto_tsquery('english', :query)
            ORDER BY rank DESC
            LIMIT :limit OFFSET :offset
        """)

        result = await self.session.execute(sql, params)
        rows = result.mappings().all()
        return [FullTextSearchResult.model_validate(dict(r)) for r in rows]

    async def semantic_search(
        self,
        embedding: List[float],
        *,
        department_code: Optional[str] = None,
        limit: int = 10,
        similarity_threshold: float = 0.7,
    ) -> List[SemanticSearchResult]:
        """
        HNSW cosine-similarity search using pgvector.
        Only searches PUBLISHED papers with non-null embeddings.
        """
        params: dict = {
            "embedding": str(embedding),
            "threshold": 1.0 - similarity_threshold,  # cosine distance threshold
            "limit": limit,
        }

        dept_filter = "AND p.department_code = :dept" if department_code else ""
        if department_code:
            params["dept"] = department_code

        sql = text(f"""
            SELECT
                p.paper_id,
                p.title,
                p.venue,
                p.year,
                p.doi,
                p.faculty_email,
                p.department_code,
                p.status,
                CAST(p.overall_confidence AS FLOAT) AS overall_confidence,
                1.0 - (p.embedding <=> :embedding ::vector) AS similarity
            FROM papers p
            WHERE
                p.status = 'PUBLISHED'
                AND p.embedding IS NOT NULL
                {dept_filter}
                AND (p.embedding <=> :embedding ::vector) <= :threshold
            ORDER BY p.embedding <=> :embedding ::vector
            LIMIT :limit
        """)

        result = await self.session.execute(sql, params)
        rows = result.mappings().all()
        return [SemanticSearchResult.model_validate(dict(r)) for r in rows]


# ── AuditLog Repository (read-only) ──────────────────────────────────────────

class AuditLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_for_resource(
        self,
        resource_type: str,
        resource_id: str,
        *,
        limit: int = 100,
    ) -> list:
        result = await self.session.execute(
            select(AuditLog)
            .where(
                and_(
                    AuditLog.resource_type == resource_type,
                    AuditLog.resource_id == resource_id,
                )
            )
            .order_by(AuditLog.logged_at.desc())
            .limit(limit)
        )
        return result.scalars().all()
