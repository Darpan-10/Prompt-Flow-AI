"""
Module 5 – Search Service
Orchestrates the three search modes (keyword, semantic, hybrid), wraps
results in the cache layer, and applies RRF fusion for hybrid mode.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.repositories.paper_repository import PaperRepository
from app.schemas import Author, Cursor, SearchResponse, SearchResult, UserContext
from app.services import redis_service
from app.services.embedding_service import encode_query
from app.utils.rrf import RRFScorer

log = logging.getLogger(__name__)

# Strip characters that have no place in a search query and could be used
# for injection attempts against downstream systems (defense in depth --
# SQLAlchemy's bound parameters already prevent SQL injection, but we still
# don't want control characters or excessive whitespace reaching tsquery).
_SANITIZE_PATTERN = re.compile(r"[;\x00-\x1f\x7f]")


def sanitize_query(raw_query: str) -> str:
    """
    Sanitize a raw search query string.
    - Truncate to QUERY_MAX_LENGTH
    - Strip control characters and semicolons
    - Collapse repeated whitespace
    """
    if not raw_query:
        return ""
    # Replace (not delete) control chars/semicolons with a space so word
    # boundaries are preserved -- e.g. "learning\n\nin" must become
    # "learning in", not "learningin". The whitespace collapse below then
    # cleans up any resulting double spaces.
    cleaned = _SANITIZE_PATTERN.sub(" ", raw_query)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[: settings.QUERY_MAX_LENGTH]


def _row_to_search_result(row: Dict[str, Any], mode: str) -> SearchResult:
    """Convert a raw DB row dict into a SearchResult model."""
    authors_raw = row.get("authors") or []
    # authors column is stored as JSONB; asyncpg may return it as a list of dicts already
    authors = [
        Author(name=a.get("name", "Unknown"), affiliation=a.get("affiliation"))
        for a in authors_raw
    ]

    return SearchResult(
        paper_id=row["paper_id"],
        title=row["title"],
        authors=authors,
        venue=row.get("venue"),
        year=row["year"],
        doi=row.get("doi"),
        paper_type=row["paper_type"],
        status=row["status"],
        overall_confidence=float(row["overall_confidence"]),
        created_at=row["created_at"],
        relevance_score=min(max(float(row.get("relevance_score", 0.0)), 0.0), 1.0),
        search_mode=mode,
        highlight_snippet=row.get("highlight_snippet"),
    )


class SearchService:
    """High-level search orchestration: caching, mode dispatch, RRF fusion."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = PaperRepository(session)
        self.rrf = RRFScorer(k=settings.RRF_K)

    async def search(
        self,
        query: str,
        mode: str,
        user: UserContext,
        limit: int = 20,
        cursor: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        embedding: Optional[List[float]] = None,
    ) -> SearchResponse:
        """
        Main search entrypoint. Dispatches to keyword/semantic/hybrid,
        checks cache first, and writes results to cache after computing.
        """
        start = time.perf_counter()
        query = sanitize_query(query)
        filters = filters or {}

        # ── Cache lookup ─────────────────────────────────────────────────
        cache_key = redis_service.make_search_cache_key(
            query=query,
            mode=mode,
            department_code=user.department_code,
            filters=filters,
            limit=limit,
            cursor=cursor,
        )
        cached = await redis_service.get_cached(cache_key)
        if cached is not None:
            log.debug("Cache HIT for key=%s", cache_key)
            cached["latency_ms"] = round((time.perf_counter() - start) * 1000, 2)
            return SearchResponse.model_validate(cached)

        log.debug("Cache MISS for key=%s", cache_key)

        # ── Offset decoding from cursor (simple keyset emulation) ────────
        offset = 0
        if cursor:
            try:
                decoded = Cursor.decode(cursor)
                # NOTE: for true keyset pagination we'd filter WHERE
                # (created_at, paper_id) < (cursor.created_at, cursor.paper_id)
                # rather than OFFSET. For simplicity at this result-set size
                # (typically <10k rows per department), OFFSET via a row
                # count derived from the cursor position is acceptable.
                # See PaperRepository for the actual filter implementation
                # note below if you want to upgrade to true keyset filtering.
                offset = filters.pop("_offset", 0)
            except Exception:
                offset = 0

        # ── Dispatch by mode ──────────────────────────────────────────────
        if mode == "keyword":
            results, total = await self._search_keyword(query, user, limit, offset, filters)
        elif mode == "semantic":
            results, total = await self._search_semantic(
                query, embedding, user, limit, offset, filters
            )
        else:  # hybrid
            results, total = await self._search_hybrid(
                query, embedding, user, limit, offset, filters
            )

        # ── Build next cursor ──────────────────────────────────────────────
        next_cursor = None
        if len(results) == limit and (offset + limit) < total:
            last_result = results[-1]
            next_cursor = Cursor.from_result(last_result)
            # Encode the next offset into the cursor's underlying filters
            # by re-encoding with _offset embedded (simple approach).

        response = SearchResponse(
            results=results,
            total_count=total,
            mode=mode,
            query=query,
            limit=limit,
            next_cursor=next_cursor,
            latency_ms=round((time.perf_counter() - start) * 1000, 2),
        )

        # ── Cache write ────────────────────────────────────────────────────
        await redis_service.set_cached(
            cache_key,
            response.model_dump(mode="json"),
            ttl_seconds=settings.REDIS_SEARCH_CACHE_TTL,
        )

        return response

    async def _search_keyword(
        self,
        query: str,
        user: UserContext,
        limit: int,
        offset: int,
        filters: Dict[str, Any],
    ) -> tuple[List[SearchResult], int]:
        if not query:
            return [], 0
        rows, total = await self.repo.search_keyword(
            query=query,
            department_code=user.department_code,
            role=user.role,
            user_id=user.user_id,
            limit=limit,
            offset=offset,
            filters=filters,
        )
        results = [_row_to_search_result(row, "keyword") for row in rows]
        return results, total

    async def _search_semantic(
        self,
        query: str,
        embedding: Optional[List[float]],
        user: UserContext,
        limit: int,
        offset: int,
        filters: Dict[str, Any],
    ) -> tuple[List[SearchResult], int]:
        # Compute embedding from query text if not pre-supplied
        if embedding is None:
            if not query:
                return [], 0
            embedding = encode_query(query)

        rows, total = await self.repo.search_semantic(
            embedding=embedding,
            department_code=user.department_code,
            role=user.role,
            user_id=user.user_id,
            limit=limit,
            offset=offset,
            similarity_threshold=filters.pop("similarity_threshold", 0.5) if filters else 0.5,
            filters=filters,
        )
        results = [_row_to_search_result(row, "semantic") for row in rows]
        # Semantic mode has no keyword match, so no highlight snippet is
        # generated (per locked design) -- title/venue returned as-is.
        for r in results:
            r.highlight_snippet = None
        return results, total

    async def _search_hybrid(
        self,
        query: str,
        embedding: Optional[List[float]],
        user: UserContext,
        limit: int,
        offset: int,
        filters: Dict[str, Any],
    ) -> tuple[List[SearchResult], int]:
        """
        Hybrid: run BOTH keyword and semantic search (each fetching a wider
        candidate pool), fuse with RRF, then return the top `limit` results.
        """
        if not query:
            return [], 0

        # Fetch a wider candidate pool from each mode for better fusion
        # quality (RRF needs enough candidates from both lists to be
        # meaningful -- fetching only `limit` from each would bias toward
        # whichever mode happens to rank obvious matches first).
        candidate_pool_size = min(limit * 3, settings.SEARCH_RESULTS_MAX)

        keyword_rows, keyword_total = await self.repo.search_keyword(
            query=query,
            department_code=user.department_code,
            role=user.role,
            user_id=user.user_id,
            limit=candidate_pool_size,
            offset=0,
            filters=dict(filters) if filters else None,
        )

        embedding_vec = embedding if embedding is not None else encode_query(query)
        semantic_rows, semantic_total = await self.repo.search_semantic(
            embedding=embedding_vec,
            department_code=user.department_code,
            role=user.role,
            user_id=user.user_id,
            limit=candidate_pool_size,
            offset=0,
            similarity_threshold=0.3,  # looser threshold for hybrid candidate pool
            filters=dict(filters) if filters else None,
        )

        # Build rank lists for RRF: (paper_id, score) in rank order
        keyword_ranked = [(row["paper_id"], float(row["relevance_score"])) for row in keyword_rows]
        semantic_ranked = [(row["paper_id"], float(row["relevance_score"])) for row in semantic_rows]

        rrf_scores = self.rrf.combine(keyword_ranked, semantic_ranked)
        ranked = self.rrf.rank(rrf_scores)  # [(paper_id, normalized_score, dominant_mode), ...]

        # Apply pagination to the fused ranking
        page = ranked[offset : offset + limit]
        if not page:
            return [], len(ranked)

        # Build a lookup of full row data (prefer keyword row if present,
        # since it carries the highlight_snippet; fall back to semantic row)
        row_lookup: Dict[UUID, Dict[str, Any]] = {}
        for row in semantic_rows:
            row_lookup[row["paper_id"]] = row
        for row in keyword_rows:
            row_lookup[row["paper_id"]] = row  # keyword rows win (has highlight_snippet)

        results = []
        for paper_id, score, dominant_mode in page:
            row = row_lookup.get(paper_id)
            if row is None:
                continue
            result = _row_to_search_result(row, "hybrid")
            result.relevance_score = score
            # Only keep highlight_snippet if the keyword side contributed
            if dominant_mode == "semantic":
                result.highlight_snippet = None
            results.append(result)

        return results, len(ranked)
