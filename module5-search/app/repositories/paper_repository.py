"""
Module 5 – Paper Repository
Read-only queries against Module 4's papers table with RLS context.
All queries SET LOCAL app.current_department and related context BEFORE executing.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)


async def set_rls_context(
    session,
    department_code: str,
    role: str,
    user_id: str,
) -> None:
    await session.execute(
        text(
            """
            SELECT
                set_config('app.current_department', :dept, true),
                set_config('app.current_role', :role, true),
                set_config('app.current_user_id', :uid, true)
            """
        ),
        {
            "dept": department_code,
            "role": role,
            "uid": user_id,
        },
    )

class PaperRepository:
    """
    Read-only access to papers table (Module 4's ownership).
    All methods enforce RLS before executing queries.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def search_keyword(
        self,
        query: str,
        department_code: str,
        role: str,
        user_id: str,
        limit: int = 20,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
    ) -> tuple[List[Dict[str, Any]], int]:
        """
        Full-text search using PostgreSQL GIN index on title || venue.
        
        Uses websearch_to_tsquery() for better query parsing (handles quotes, AND/OR).
        Returns list of papers + total count.
        """
        # Set RLS context
        await set_rls_context(self.session, department_code, role, user_id)

        # Build query
        sql = """
        SELECT
            p.paper_id,
            p.title,
            p.authors,
            p.venue,
            p.year,
            p.doi,
            p.paper_type,
            p.status,
            p.overall_confidence,
            p.created_at,
            p.faculty_id,
            ts_rank_cd(
                to_tsvector('english', p.title || ' ' || COALESCE(p.venue, '')),
                websearch_to_tsquery('english', :query)
            ) AS relevance_score,
            ts_headline(
                'english',
                p.title || ' ' || COALESCE(p.venue, ''),
                websearch_to_tsquery('english', :query),
                'StartSel=<mark>, StopSel=</mark>, MaxWords=20, MinWords=5'
            ) AS highlight_snippet
        FROM papers p
        WHERE
            to_tsvector('english', p.title || ' ' || COALESCE(p.venue, ''))
            @@ websearch_to_tsquery('english', :query)
            AND p.status = 'PUBLISHED'
        """

        # Apply facet filters
        if filters:
            if filters.get("year"):
                sql += f" AND p.year = :year"
            if filters.get("paper_type"):
                sql += f" AND p.paper_type = :paper_type"
            if filters.get("min_confidence"):
                sql += f" AND p.overall_confidence >= :min_confidence"

        sql += " ORDER BY relevance_score DESC LIMIT :limit OFFSET :offset"

        # Execute search
        result = await self.session.execute(
            text(sql),
            {
                "query": query,
                "limit": limit,
                "offset": offset,
                **(filters or {}),
            },
        )
        rows = result.fetchall()

        # Get total count
        count_sql = """
        SELECT COUNT(*)
        FROM papers p
        WHERE
            to_tsvector('english', p.title || ' ' || COALESCE(p.venue, ''))
            @@ websearch_to_tsquery('english', :query)
            AND p.status = 'PUBLISHED'
        """
        if filters:
            if filters.get("year"):
                count_sql += f" AND p.year = :year"
            if filters.get("paper_type"):
                count_sql += f" AND p.paper_type = :paper_type"
            if filters.get("min_confidence"):
                count_sql += f" AND p.overall_confidence >= :min_confidence"

        count_result = await self.session.execute(
            text(count_sql),
            {"query": query, **(filters or {})},
        )
        total = count_result.scalar() or 0

        return [dict(row._mapping) for row in rows], total

    async def search_semantic(
        self,
        embedding: List[float],
        department_code: str,
        role: str,
        user_id: str,
        limit: int = 20,
        offset: int = 0,
        similarity_threshold: float = 0.5,
        filters: Optional[Dict[str, Any]] = None,
    ) -> tuple[List[Dict[str, Any]], int]:
        """
        Vector similarity search using pgvector HNSW index.
        
        Uses cosine similarity (<=> operator) on 768-dim embeddings.
        Only searches PUBLISHED papers.
        """
        # Set RLS context
        await set_rls_context(self.session, department_code, role, user_id)

        # Convert embedding to pgvector format
        embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"

        # Build query
        sql = f"""
        SELECT
            p.paper_id,
            p.title,
            p.authors,
            p.venue,
            p.year,
            p.doi,
            p.paper_type,
            p.status,
            p.overall_confidence,
            p.created_at,
            p.faculty_id,
            (1 - (p.embedding <=> '{embedding_str}'::vector)) AS relevance_score
        FROM papers p
        WHERE
            p.status = 'PUBLISHED'
            AND p.embedding IS NOT NULL
            AND (1 - (p.embedding <=> '{embedding_str}'::vector)) >= :similarity_threshold
        """

        # Apply facet filters
        if filters:
            if filters.get("year"):
                sql += f" AND p.year = :year"
            if filters.get("paper_type"):
                sql += f" AND p.paper_type = :paper_type"
            if filters.get("min_confidence"):
                sql += f" AND p.overall_confidence >= :min_confidence"

        sql += f" ORDER BY relevance_score DESC LIMIT :limit OFFSET :offset"

        result = await self.session.execute(
            text(sql),
            {
                "similarity_threshold": similarity_threshold,
                "limit": limit,
                "offset": offset,
                **(filters or {}),
            },
        )
        rows = result.fetchall()

        # Get total count
        count_sql = f"""
        SELECT COUNT(*)
        FROM papers p
        WHERE
            p.status = 'PUBLISHED'
            AND p.embedding IS NOT NULL
            AND (1 - (p.embedding <=> '{embedding_str}'::vector)) >= :similarity_threshold
        """
        if filters:
            if filters.get("year"):
                count_sql += f" AND p.year = :year"
            if filters.get("paper_type"):
                count_sql += f" AND p.paper_type = :paper_type"
            if filters.get("min_confidence"):
                count_sql += f" AND p.overall_confidence >= :min_confidence"

        count_result = await self.session.execute(
            text(count_sql),
            {"similarity_threshold": similarity_threshold, **(filters or {})},
        )
        total = count_result.scalar() or 0

        return [dict(row._mapping) for row in rows], total

    async def get_papers_by_ids(
        self,
        paper_ids: List[UUID],
        department_code: str,
        role: str,
        user_id: str,
    ) -> List[Dict[str, Any]]:
        """
        Fetch papers by ID list with RLS enforcement.
        Used after RRF fusion to retrieve final metadata.
        """
        # Set RLS context
        await set_rls_context(self.session, department_code, role, user_id)

        # Convert UUIDs to strings for SQL IN clause
        id_list = ",".join(f"'{str(pid)}'" for pid in paper_ids)

        sql = f"""
        SELECT
            paper_id, title, authors, venue, year, doi, paper_type,
            status, overall_confidence, created_at, faculty_id
        FROM papers
        WHERE paper_id IN ({id_list})
        """

        result = await self.session.execute(text(sql))
        return [dict(row._mapping) for row in result.fetchall()]

    async def get_facet_counts(
        self,
        department_code: str,
        role: str,
        user_id: str,
    ) -> Dict[str, Any]:
        """
        Aggregate facet counts (year, paper_type, confidence ranges).
        Heavily cached (1-hour TTL) since these change infrequently.
        """
        # Set RLS context
        await set_rls_context(self.session, department_code, role, user_id)

        # Year counts
        year_sql = """
        SELECT year, COUNT(*) as count
        FROM papers
        WHERE status = 'PUBLISHED'
        GROUP BY year
        ORDER BY year DESC
        """
        year_result = await self.session.execute(text(year_sql))
        year_counts = [{"value": str(row[0]), "count": row[1]} for row in year_result]

        # Paper type counts
        type_sql = """
        SELECT paper_type, COUNT(*) as count
        FROM papers
        WHERE status = 'PUBLISHED'
        GROUP BY paper_type
        ORDER BY paper_type
        """
        type_result = await self.session.execute(text(type_sql))
        type_counts = [{"value": row[0], "count": row[1]} for row in type_result]

        # Confidence range counts
        confidence_sql = """
        SELECT
            CASE
                WHEN overall_confidence < 0.5 THEN '0.0-0.5'
                WHEN overall_confidence < 0.7 THEN '0.5-0.7'
                WHEN overall_confidence < 0.9 THEN '0.7-0.9'
                ELSE '0.9-1.0'
            END as range,
            COUNT(*) as count
        FROM papers
        WHERE status = 'PUBLISHED'
        GROUP BY range
        ORDER BY range
        """
        conf_result = await self.session.execute(text(confidence_sql))
        conf_counts = [{"value": row[0], "count": row[1]} for row in conf_result]

        return {
            "years": year_counts,
            "paper_types": type_counts,
            "confidence_ranges": conf_counts,
        }

    async def get_suggestions(
        self,
        prefix: str,
        suggestion_type: str,
        department_code: str,
        role: str,
        user_id: str,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Autocomplete suggestions for title, author name, or venue.
        Uses ILIKE prefix matching (case-insensitive), backed by the
        existing B-tree/GIN indexes -- fast enough for short prefixes
        given the dataset size (thousands, not millions, of papers).
        """
        await set_rls_context(self.session, department_code, role, user_id)

        prefix_pattern = f"{prefix}%"

        if suggestion_type == "title":
            sql = """
            SELECT title AS text, COUNT(*) OVER (PARTITION BY title) as frequency
            FROM papers
            WHERE title ILIKE :prefix AND status = 'PUBLISHED'
            ORDER BY title
            LIMIT :limit
            """
        elif suggestion_type == "venue":
            sql = """
            SELECT DISTINCT venue AS text, COUNT(*) OVER (PARTITION BY venue) as frequency
            FROM papers
            WHERE venue ILIKE :prefix AND status = 'PUBLISHED' AND venue IS NOT NULL
            ORDER BY venue
            LIMIT :limit
            """
        elif suggestion_type == "author":
            # authors is JSONB array; use jsonb_array_elements to unnest names
            sql = """
            SELECT DISTINCT author_elem->>'name' AS text, COUNT(*) as frequency
            FROM papers, jsonb_array_elements(authors) AS author_elem
            WHERE author_elem->>'name' ILIKE :prefix AND status = 'PUBLISHED'
            GROUP BY author_elem->>'name'
            ORDER BY author_elem->>'name'
            LIMIT :limit
            """
        else:
            return []

        result = await self.session.execute(
            text(sql), {"prefix": prefix_pattern, "limit": limit}
        )
        return [dict(row._mapping) for row in result.fetchall()]
