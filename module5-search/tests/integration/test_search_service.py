"""
Module 5 – Integration Tests: SearchService
Tests the full search() orchestration (mode dispatch, caching, RRF fusion)
with the database repository and Redis mocked out -- these tests validate
MY orchestration logic, not PostgreSQL/Redis themselves.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.schemas import UserContext
from app.services.search_service import SearchService


def make_paper_row(paper_id=None, title="Test Paper", relevance=0.8, highlight=None):
    return {
        "paper_id": paper_id or uuid.uuid4(),
        "title": title,
        "authors": [{"name": "Dr. Test", "affiliation": "SRM AP"}],
        "venue": "Test Venue",
        "year": 2024,
        "doi": "10.1234/test",
        "paper_type": "journal",
        "status": "PUBLISHED",
        "overall_confidence": 0.9,
        "created_at": datetime.now(timezone.utc),
        "faculty_id": uuid.uuid4(),
        "relevance_score": relevance,
        "highlight_snippet": highlight,
    }


@pytest.fixture
def user_ctx():
    return UserContext(user_id="u1", department_code="CSE", role="admin", is_admin=True)


@pytest.fixture(autouse=True)
def mock_redis_cache():
    """Mock Redis cache as always-empty (cache miss) + no-op writes, for every test."""
    with patch("app.services.search_service.redis_service.get_cached", new=AsyncMock(return_value=None)), \
         patch("app.services.search_service.redis_service.set_cached", new=AsyncMock()), \
         patch("app.services.search_service.redis_service.make_search_cache_key", return_value="search:test"):
        yield


class TestSearchServiceKeywordMode:
    @pytest.mark.asyncio
    async def test_keyword_mode_calls_repo_search_keyword(self, user_ctx):
        service = SearchService(session=AsyncMock())
        row = make_paper_row(highlight="<mark>Attention</mark> Is All You Need")

        with patch.object(service.repo, "search_keyword", new=AsyncMock(return_value=([row], 1))):
            response = await service.search(query="attention", mode="keyword", user=user_ctx, limit=20)

        assert response.mode == "keyword"
        assert response.total_count == 1
        assert len(response.results) == 1
        assert response.results[0].search_mode == "keyword"
        assert response.results[0].highlight_snippet is not None

    @pytest.mark.asyncio
    async def test_empty_query_keyword_returns_empty(self, user_ctx):
        service = SearchService(session=AsyncMock())
        response = await service.search(query="", mode="keyword", user=user_ctx, limit=20)
        assert response.results == []
        assert response.total_count == 0


class TestSearchServiceSemanticMode:
    @pytest.mark.asyncio
    async def test_semantic_mode_computes_embedding_when_not_provided(self, user_ctx):
        service = SearchService(session=AsyncMock())
        row = make_paper_row()

        with patch.object(service.repo, "search_semantic", new=AsyncMock(return_value=([row], 1))) as mock_search, \
             patch("app.services.search_service.encode_query", return_value=[0.1] * 768) as mock_encode:
            response = await service.search(query="neural networks", mode="semantic", user=user_ctx, limit=20)

        mock_encode.assert_called_once_with("neural networks")
        assert mock_search.called
        assert response.mode == "semantic"
        # Semantic mode never returns a highlight snippet (no keyword match)
        assert response.results[0].highlight_snippet is None

    @pytest.mark.asyncio
    async def test_semantic_mode_uses_provided_embedding_without_encoding(self, user_ctx):
        service = SearchService(session=AsyncMock())
        row = make_paper_row()
        provided_embedding = [0.5] * 768

        with patch.object(service.repo, "search_semantic", new=AsyncMock(return_value=([row], 1))), \
             patch("app.services.search_service.encode_query") as mock_encode:
            await service.search(
                query="ignored text", mode="semantic", user=user_ctx, limit=20,
                embedding=provided_embedding,
            )

        # encode_query should NOT be called -- embedding was pre-supplied
        mock_encode.assert_not_called()


class TestSearchServiceHybridMode:
    @pytest.mark.asyncio
    async def test_hybrid_mode_fuses_both_result_sets(self, user_ctx):
        service = SearchService(session=AsyncMock())

        shared_id = uuid.uuid4()
        keyword_only_id = uuid.uuid4()
        semantic_only_id = uuid.uuid4()

        keyword_rows = [
            make_paper_row(paper_id=shared_id, relevance=0.9, highlight="<mark>match</mark>"),
            make_paper_row(paper_id=keyword_only_id, relevance=0.7),
        ]
        semantic_rows = [
            make_paper_row(paper_id=shared_id, relevance=0.95),
            make_paper_row(paper_id=semantic_only_id, relevance=0.6),
        ]

        with patch.object(service.repo, "search_keyword", new=AsyncMock(return_value=(keyword_rows, 2))), \
             patch.object(service.repo, "search_semantic", new=AsyncMock(return_value=(semantic_rows, 2))), \
             patch("app.services.search_service.encode_query", return_value=[0.1] * 768):
            response = await service.search(query="transformers", mode="hybrid", user=user_ctx, limit=20)

        assert response.mode == "hybrid"
        result_ids = {r.paper_id for r in response.results}
        # All three distinct papers should appear (union of both lists)
        assert shared_id in result_ids
        assert keyword_only_id in result_ids
        assert semantic_only_id in result_ids

        # The paper that appeared in BOTH lists should rank first (highest RRF score)
        assert response.results[0].paper_id == shared_id

    @pytest.mark.asyncio
    async def test_hybrid_mode_preserves_highlight_only_for_keyword_dominant(self, user_ctx):
        service = SearchService(session=AsyncMock())
        semantic_only_id = uuid.uuid4()

        keyword_rows = []
        semantic_rows = [make_paper_row(paper_id=semantic_only_id, relevance=0.9)]

        with patch.object(service.repo, "search_keyword", new=AsyncMock(return_value=(keyword_rows, 0))), \
             patch.object(service.repo, "search_semantic", new=AsyncMock(return_value=(semantic_rows, 1))), \
             patch("app.services.search_service.encode_query", return_value=[0.1] * 768):
            response = await service.search(query="test", mode="hybrid", user=user_ctx, limit=20)

        # Paper only found via semantic search should have no highlight
        assert response.results[0].highlight_snippet is None


class TestSearchServiceCaching:
    @pytest.mark.asyncio
    async def test_cache_hit_skips_repository_call(self, user_ctx):
        service = SearchService(session=AsyncMock())
        cached_response = {
            "results": [], "total_count": 0, "mode": "keyword",
            "query": "cached query", "limit": 20, "next_cursor": None,
            "facets": None, "latency_ms": 1.0,
        }

        with patch("app.services.search_service.redis_service.get_cached", new=AsyncMock(return_value=cached_response)), \
             patch.object(service.repo, "search_keyword", new=AsyncMock()) as mock_repo_call:
            response = await service.search(query="cached query", mode="keyword", user=user_ctx, limit=20)

        # Repository should NEVER be called on a cache hit
        mock_repo_call.assert_not_called()
        assert response.query == "cached query"
