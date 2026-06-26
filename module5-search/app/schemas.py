"""
Module 5 – Schemas
Pydantic V2 models for search requests/responses, pagination, facets.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


# ── Sub-models ────────────────────────────────────────────────────────────

class Author(BaseModel):
    """Paper author with optional affiliation."""
    name: str = Field(..., min_length=1, max_length=300)
    affiliation: Optional[str] = None

    model_config = {"from_attributes": True}


# ── Search Request Models ─────────────────────────────────────────────────

class SearchRequest(BaseModel):
    """
    General search request supporting all three modes.
    
    For SEMANTIC mode, either provide `embedding` directly (if pre-computed),
    or leave it null and the service will compute it from `query`.
    """
    query: str = Field(..., min_length=0, max_length=200, description="Search query or semantic query text")
    mode: Literal["keyword", "semantic", "hybrid"] = Field(default="hybrid")
    embedding: Optional[List[float]] = Field(None, description="Pre-computed 768-dim embedding (optional)")
    
    # Pagination
    limit: int = Field(default=20, ge=1, le=100)
    cursor: Optional[str] = Field(None, description="Base64-encoded cursor for next page")
    
    # Faceted filters
    department_code: Optional[str] = None
    year: Optional[int] = Field(None, ge=2000)
    paper_type: Optional[Literal["journal", "conference", "thesis", "book_chapter"]] = None
    status: Optional[Literal["PUBLISHED", "DRAFT"]] = Field(default="PUBLISHED")
    faculty_id: Optional[UUID] = None
    min_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    
    @field_validator("embedding")
    @classmethod
    def validate_embedding_dim(cls, v: Optional[List[float]]) -> Optional[List[float]]:
        if v is not None and len(v) != 768:
            raise ValueError(f"Embedding must be 768-dimensional, got {len(v)}")
        return v


class SemanticSearchRequest(BaseModel):
    """Explicit semantic search with pre-computed embedding vector."""
    embedding: List[float] = Field(..., min_length=768, max_length=768)
    limit: int = Field(default=20, ge=1, le=100)
    cursor: Optional[str] = None
    department_code: Optional[str] = None
    min_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    similarity_threshold: float = Field(default=0.5, ge=0.0, le=1.0)


# ── Search Result Models ──────────────────────────────────────────────────

class SearchResult(BaseModel):
    """
    A single search result.
    
    relevance_score: normalized 0.0-1.0 (from RRF, ts_rank_cd, or cosine similarity)
    highlight_snippet: Optional matched context (from ts_headline for keyword mode)
    """
    paper_id: UUID
    title: str
    authors: List[Author]
    venue: Optional[str]
    year: int
    doi: Optional[str]
    paper_type: str
    status: str
    overall_confidence: float
    created_at: datetime
    
    relevance_score: float = Field(..., ge=0.0, le=1.0, description="Normalized score")
    search_mode: Literal["keyword", "semantic", "hybrid"]
    highlight_snippet: Optional[str] = Field(None, description="Matched text snippet with highlights")

    model_config = {"from_attributes": True}


class Cursor(BaseModel):
    """Keyset pagination cursor (base64-encoded JSON)."""
    paper_id: UUID
    created_at: datetime
    
    @classmethod
    def from_result(cls, result: SearchResult) -> str:
        """Encode cursor as base64 JSON."""
        import base64
        import json
        payload = {
            "paper_id": str(result.paper_id),
            "created_at": result.created_at.isoformat(),
        }
        return base64.b64encode(json.dumps(payload).encode()).decode()
    
    @classmethod
    def decode(cls, cursor: str) -> "Cursor":
        """Decode base64 JSON cursor."""
        import base64
        import json
        from datetime import datetime as dt
        payload = json.loads(base64.b64decode(cursor.encode()))
        return cls(
            paper_id=UUID(payload["paper_id"]),
            created_at=dt.fromisoformat(payload["created_at"]),
        )


class SearchResponse(BaseModel):
    """Full search response with pagination and facets."""
    results: List[SearchResult]
    total_count: int
    mode: Literal["keyword", "semantic", "hybrid"]
    query: str
    limit: int
    next_cursor: Optional[str] = Field(None, description="Cursor for next page, null if last page")
    facets: Optional[Dict[str, Any]] = None  # Facet counts if requested
    latency_ms: float  # For monitoring


# ── Facet Models ──────────────────────────────────────────────────────────

class FacetCount(BaseModel):
    """Count for a single facet value (e.g., year=2024, count=42)."""
    value: str
    count: int


class FacetCounts(BaseModel):
    """Aggregated facet counts, cached per department."""
    years: List[FacetCount]
    paper_types: List[FacetCount]
    confidence_ranges: List[FacetCount]  # e.g., ["0.0-0.5", "0.5-0.7", "0.7-0.9", "0.9-1.0"]
    status_counts: Dict[str, int]  # {"PUBLISHED": 234, "DRAFT": 12}


# ── Autocomplete Models ───────────────────────────────────────────────────

class SuggestionRequest(BaseModel):
    """Autocomplete suggestion request."""
    prefix: str = Field(..., min_length=1, max_length=50)
    type: Literal["title", "author", "venue"] = "title"
    limit: int = Field(default=10, ge=1, le=50)


class Suggestion(BaseModel):
    """A single autocomplete suggestion."""
    text: str
    type: str
    frequency: int  # How many papers match this


# ── Health Models ─────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str
    service: str = "module5-search"
    database: bool
    redis: bool
    embedding_model_loaded: bool


# ── Error Models ──────────────────────────────────────────────────────────

class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None
    request_id: Optional[str] = None


# ── Auth Context (from JWT, used internally) ─────────────────────────────

class UserContext(BaseModel):
    """
    User context extracted from JWT claims.
    Set by dependency injection before every search request.
    """
    user_id: str
    department_code: str
    role: Literal["faculty", "coordinator", "hod", "admin", "system_worker"]
    faculty_id: Optional[UUID] = None  # If user is faculty
    is_admin: bool = False

    @property
    def is_faculty_self(self, faculty_id: UUID) -> bool:
        """Check if user is the paper author."""
        return self.faculty_id == faculty_id and self.role == "faculty"
