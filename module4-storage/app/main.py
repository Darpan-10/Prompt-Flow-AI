"""
Module 4 – FastAPI Application
Serves search and export endpoints for Modules 5 & 6.
"""

from __future__ import annotations

import logging
import sys
import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator, List, Optional
from uuid import UUID

import redis.asyncio as aioredis
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import check_db_health, get_db, set_rls_context, set_admin_context
from app.models.schemas import (
    FullTextSearchRequest,
    FullTextSearchResult,
    HealthResponse,
    PaginatedResponse,
    PaperCreate,
    PaperRead,
    PaperUpdate,
    PaperVersionRead,
    SemanticSearchRequest,
    SemanticSearchResult,
    ValidationIssueRead,
    ValidationIssueResolve,
)
from app.repository.repository import (
    AuditLogRepository,
    PaperRepository,
    PaperVersionRepository,
    SearchRepository,
    ValidationIssueRepository,
)

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    log.info("Module 4 Storage API starting up...")
    yield
    log.info("Module 4 Storage API shutting down...")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="PromptFlow AI – Module 4: Storage & Indexing",
    version="1.0.0",
    description="Secure paper storage with partitioning, versioning, RLS, and search.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── RLS middleware (injects department context from request headers) ───────────

# Roles Module 1 is expected to ever issue. Anything outside this set
# gets rejected rather than silently trusted -- a typo'd or otherwise
# unexpected role string reaching set_rls_context() unchecked could
# interact with the RLS policy's `current_role = 'admin'` comparison in
# ways nobody has actually reviewed.
_VALID_ROLES = {"faculty", "coordinator", "hod", "admin", "system_worker"}


async def rls_context(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AsyncSession:
    """
    Inject RLS context per-request from upstream-authenticated headers.

    CRITICAL FIX (found via a full cross-module RLS security sweep): this
    function used to default to `department="__admin__"` and
    `role="admin"` whenever the X-Department-Code/X-Role/X-User-Id
    headers were simply ABSENT (not malformed -- just missing, which is
    the default for any request that doesn't explicitly set them),
    unconditionally granting full cross-department admin access via
    set_admin_context(). Every data-touching route in this file uses
    this dependency, meaning the entire API was open-by-default to
    anyone who omitted these three headers. Now: missing or empty
    headers are rejected with 401. The old "default to admin" behavior
    is available ONLY behind settings.ALLOW_MISSING_AUTH_HEADERS=true,
    which must be false in any deployed environment (see config.py).

    Module 4 trusts these headers as being injected by an upstream
    authenticated gateway (Module 1) rather than parsing a JWT itself --
    that trust-boundary architecture is unchanged; only the "what happens
    when the headers aren't there" behavior changed.
    """
    department = request.headers.get("X-Department-Code")
    role = request.headers.get("X-Role")
    user_id = request.headers.get("X-User-Id")

    if not department or not role or not user_id:
        if settings.ALLOW_MISSING_AUTH_HEADERS:
            # Local-dev-only escape hatch -- NEVER true in a deployed
            # environment. Falls back to a clearly-fake, clearly-logged
            # identity rather than silently becoming admin.
            log.warning(
                "ALLOW_MISSING_AUTH_HEADERS=true: request missing auth "
                "headers, using local-dev fallback identity. This MUST "
                "NOT happen in production."
            )
            await set_rls_context(
                db, department_code="__local_dev__", role="admin",
                user_id="local-dev-no-headers", actor_type="user",
            )
            return db
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing required auth headers: X-Department-Code, X-Role, X-User-Id",
        )

    if role not in _VALID_ROLES:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid X-Role header: {role!r}",
        )

    if role == "admin":
        await set_admin_context(db)
    else:
        await set_rls_context(
            db,
            department_code=department,
            role=role,
            user_id=user_id,
            actor_type="user",
        )
    return db


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse, tags=["Health"])
async def health():
    db_ok = await check_db_health()
    try:
        r = await aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        await r.ping()
        redis_ok = True
        await r.aclose()
    except Exception:
        redis_ok = False

    overall = "ok" if (db_ok and redis_ok) else "degraded"
    return HealthResponse(
        status=overall,
        database=db_ok,
        kafka=True,  # consumer is separate process
        redis=redis_ok,
    )


@app.get("/ready", tags=["Health"])
async def ready():
    if not await check_db_health():
        raise HTTPException(status_code=503, detail="Database not ready")
    return {"status": "ready"}


# ── Papers ────────────────────────────────────────────────────────────────────

@app.post(
    "/api/v1/papers",
    response_model=PaperRead,
    status_code=status.HTTP_201_CREATED,
    tags=["Papers"],
)
async def create_paper(
    data: PaperCreate,
    db: AsyncSession = Depends(rls_context),
):
    """Create a paper directly (used by Kafka consumer internally or for testing)."""
    repo = PaperRepository(db)
    paper, created = await repo.upsert_on_conflict_ignore(data)
    if not created:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Paper with idempotency_key={data.ingestion_idempotency_key} already exists",
        )
    return paper


@app.get(
    "/api/v1/papers/{paper_id}",
    response_model=PaperRead,
    tags=["Papers"],
)
async def get_paper(
    paper_id: UUID,
    db: AsyncSession = Depends(rls_context),
):
    repo = PaperRepository(db)
    paper = await repo.get_by_id(paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")
    return paper


@app.get(
    "/api/v1/papers",
    response_model=List[PaperRead],
    tags=["Papers"],
)
async def list_papers(
    department_code: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    year: Optional[int] = Query(None),
    faculty_id: Optional[UUID] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(rls_context),
):
    repo = PaperRepository(db)
    return await repo.list(
        department_code=department_code,
        status=status,
        year=year,
        faculty_id=faculty_id,
        limit=limit,
        offset=offset,
    )


@app.patch(
    "/api/v1/papers/{paper_id}",
    response_model=PaperRead,
    tags=["Papers"],
)
async def update_paper(
    paper_id: UUID,
    data: PaperUpdate,
    db: AsyncSession = Depends(rls_context),
):
    """Update paper – triggers versioning automatically via DB trigger."""
    repo = PaperRepository(db)
    paper = await repo.update(paper_id, data)
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")
    return paper


# ── Versions ──────────────────────────────────────────────────────────────────

@app.get(
    "/api/v1/papers/{paper_id}/versions",
    response_model=List[PaperVersionRead],
    tags=["Versions"],
)
async def get_paper_versions(
    paper_id: UUID,
    db: AsyncSession = Depends(rls_context),
):
    repo = PaperVersionRepository(db)
    return await repo.list_versions(paper_id)


@app.get(
    "/api/v1/papers/{paper_id}/versions/{version_number}",
    response_model=PaperVersionRead,
    tags=["Versions"],
)
async def get_paper_version(
    paper_id: UUID,
    version_number: int,
    db: AsyncSession = Depends(rls_context),
):
    repo = PaperVersionRepository(db)
    version = await repo.get_version(paper_id, version_number)
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")
    return version


# ── Validation Issues ─────────────────────────────────────────────────────────

@app.get(
    "/api/v1/papers/{paper_id}/issues",
    response_model=List[ValidationIssueRead],
    tags=["Validation"],
)
async def get_validation_issues(
    paper_id: UUID,
    db: AsyncSession = Depends(rls_context),
):
    repo = ValidationIssueRepository(db)
    return await repo.list_for_paper(paper_id)


@app.patch(
    "/api/v1/issues/{issue_id}/resolve",
    response_model=ValidationIssueRead,
    tags=["Validation"],
)
async def resolve_issue(
    issue_id: UUID,
    data: ValidationIssueResolve,
    db: AsyncSession = Depends(rls_context),
):
    repo = ValidationIssueRepository(db)
    issue = await repo.resolve(issue_id, data.resolved_by)
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")
    return issue


# ── Full-text Search ──────────────────────────────────────────────────────────

@app.post(
    "/api/v1/search/fulltext",
    response_model=List[FullTextSearchResult],
    tags=["Search"],
)
async def fulltext_search(
    req: FullTextSearchRequest,
    db: AsyncSession = Depends(rls_context),
):
    """
    GIN full-text search on title + venue (weighted A/B).
    Only searches PUBLISHED papers. Scores by ts_rank_cd.
    """
    repo = SearchRepository(db)
    return await repo.fulltext_search(
        req.query,
        department_code=req.department_code,
        year=req.year,
        status=req.status or "PUBLISHED",
        limit=req.limit,
        offset=req.offset,
    )


@app.get(
    "/api/v1/search/fulltext",
    response_model=List[FullTextSearchResult],
    tags=["Search"],
)
async def fulltext_search_get(
    q: str = Query(..., min_length=2, description="Search query"),
    department_code: Optional[str] = Query(None),
    year: Optional[int] = Query(None),
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(rls_context),
):
    """GET variant of full-text search (convenient for browser testing)."""
    repo = SearchRepository(db)
    return await repo.fulltext_search(
        q,
        department_code=department_code,
        year=year,
        limit=limit,
        offset=offset,
    )


# ── Semantic Search ───────────────────────────────────────────────────────────

@app.post(
    "/api/v1/search/semantic",
    response_model=List[SemanticSearchResult],
    tags=["Search"],
)
async def semantic_search(
    req: SemanticSearchRequest,
    db: AsyncSession = Depends(rls_context),
):
    """
    HNSW cosine-similarity search via pgvector.
    Requires embedding vector (768-dim). Only searches PUBLISHED papers.
    """
    repo = SearchRepository(db)
    return await repo.semantic_search(
        req.embedding,
        department_code=req.department_code,
        limit=req.limit,
        similarity_threshold=req.similarity_threshold,
    )


# ── Export ────────────────────────────────────────────────────────────────────

@app.get(
    "/api/v1/export/papers",
    tags=["Export"],
    summary="Export papers as NDJSON (for Module 6)",
)
async def export_papers(
    department_code: Optional[str] = Query(None),
    status: Optional[str] = Query("PUBLISHED"),
    year: Optional[int] = Query(None),
    limit: int = Query(1000, ge=1, le=10000),
    db: AsyncSession = Depends(rls_context),
):
    """
    Streaming NDJSON export for Module 6 Analytics.
    Each line is a complete paper JSON object.
    """
    import orjson

    repo = PaperRepository(db)
    papers = await repo.list(
        department_code=department_code,
        status=status,
        year=year,
        limit=limit,
    )

    def generate():
        for p in papers:
            yield orjson.dumps(p.model_dump(mode="json")).decode() + "\n"

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": "attachment; filename=papers.ndjson"},
    )


@app.get(
    "/api/v1/export/papers/csv",
    tags=["Export"],
    summary="Export papers as CSV",
)
async def export_papers_csv(
    department_code: Optional[str] = Query(None),
    status: Optional[str] = Query("PUBLISHED"),
    year: Optional[int] = Query(None),
    limit: int = Query(1000, ge=1, le=10000),
    db: AsyncSession = Depends(rls_context),
):
    """CSV export for Module 6 dashboard / NAAC reporting."""
    import csv
    import io

    repo = PaperRepository(db)
    papers = await repo.list(
        department_code=department_code,
        status=status,
        year=year,
        limit=limit,
    )

    def generate():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            "paper_id", "title", "venue", "year", "doi", "paper_type",
            "faculty_email", "department_code", "status", "overall_confidence",
            "created_at",
        ])
        for p in papers:
            writer.writerow([
                str(p.paper_id), p.title, p.venue or "", p.year, p.doi or "",
                p.paper_type, p.faculty_email, p.department_code, p.status,
                float(p.overall_confidence), p.created_at.isoformat(),
            ])
        yield buf.getvalue()

    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=papers.csv"},
    )


# ── Audit Log ─────────────────────────────────────────────────────────────────

@app.get(
    "/api/v1/audit/{resource_type}/{resource_id}",
    tags=["Audit"],
)
async def get_audit_log(
    resource_type: str,
    resource_id: str,
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(rls_context),
):
    """Read audit log for a resource (admin only in production)."""
    repo = AuditLogRepository(db)
    logs = await repo.list_for_resource(resource_type, resource_id, limit=limit)
    return [
        {
            "log_id": str(entry.log_id),
            "logged_at": entry.logged_at.isoformat(),
            "action": entry.action,
            "actor_id": entry.actor_id,
            "details": entry.details,
        }
        for entry in logs
    ]
