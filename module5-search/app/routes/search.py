"""
Module 5 – Search Routes
POST /search, GET /search/facets, GET /search/suggestions
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.config import settings
from app.database import get_db
from app.repositories.paper_repository import PaperRepository
from app.schemas import (
    FacetCounts,
    SearchRequest,
    SearchResponse,
    Suggestion,
    UserContext,
)
from app.services import redis_service
from app.services.search_service import SearchService

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/search", tags=["search"])


@router.post("", response_model=SearchResponse)
async def search(
    request: SearchRequest,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SearchResponse:
    """
    Main search endpoint. Supports keyword, semantic, and hybrid modes.

    - keyword: PostgreSQL full-text search (GIN index), with ts_headline highlights
    - semantic: pgvector cosine similarity (HNSW index)
    - hybrid (default): both modes fused via Reciprocal Rank Fusion (RRF)

    RLS is enforced transparently using the JWT's department_code/role/sub
    claims -- results are automatically scoped to what the user is allowed
    to see (their department, or all departments if admin).
    """
    service = SearchService(db)

    filters = {}
    if request.department_code:
        # Admins may search across departments by passing department_code
        # explicitly; non-admins are still bounded by RLS regardless.
        filters["department_code"] = request.department_code
    if request.year:
        filters["year"] = request.year
    if request.paper_type:
        filters["paper_type"] = request.paper_type
    if request.min_confidence:
        filters["min_confidence"] = request.min_confidence

    response = await service.search(
        query=request.query,
        mode=request.mode,
        user=user,
        limit=request.limit,
        cursor=request.cursor,
        filters=filters,
        embedding=request.embedding,
    )
    return response


@router.get("/facets", response_model=FacetCounts)
async def get_facets(
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FacetCounts:
    """
    Aggregated facet counts (year, paper_type, confidence ranges) for the
    user's department. Heavily cached (1-hour TTL) since these change
    infrequently relative to individual search queries.
    """
    cache_key = redis_service.make_facets_cache_key(user.department_code)
    cached = await redis_service.get_cached(cache_key)
    if cached is not None:
        return FacetCounts.model_validate(cached)

    repo = PaperRepository(db)
    raw_counts = await repo.get_facet_counts(
        department_code=user.department_code,
        role=user.role,
        user_id=user.user_id,
    )

    facets = FacetCounts(
        years=raw_counts["years"],
        paper_types=raw_counts["paper_types"],
        confidence_ranges=raw_counts["confidence_ranges"],
        status_counts={"PUBLISHED": sum(y["count"] for y in raw_counts["years"])},
    )

    await redis_service.set_cached(
        cache_key,
        facets.model_dump(mode="json"),
        ttl_seconds=settings.REDIS_FACETS_CACHE_TTL,
    )

    return facets


@router.get("/suggestions", response_model=list[Suggestion])
async def get_suggestions(
    prefix: str = Query(..., min_length=1, max_length=50),
    type: str = Query(default="title", pattern="^(title|author|venue)$"),
    limit: int = Query(default=10, ge=1, le=50),
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Suggestion]:
    """
    Autocomplete suggestions for search-as-you-type UX.
    Not cached (queries are cheap prefix-index lookups, and caching every
    possible prefix combination would have poor hit rates).
    """
    repo = PaperRepository(db)
    rows = await repo.get_suggestions(
        prefix=prefix,
        suggestion_type=type,
        department_code=user.department_code,
        role=user.role,
        user_id=user.user_id,
        limit=limit,
    )
    return [
        Suggestion(text=row["text"], type=type, frequency=row["frequency"])
        for row in rows
        if row.get("text")
    ]
