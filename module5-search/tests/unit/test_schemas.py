"""
Module 5 – Unit Tests: Schema Validation
"""

import pytest
from pydantic import ValidationError

from app.schemas import SearchRequest, SemanticSearchRequest, UserContext


class TestSearchRequestValidation:
    def test_valid_minimal_request(self):
        req = SearchRequest(query="attention mechanism")
        assert req.mode == "hybrid"  # default
        assert req.limit == 20  # default
        assert req.status == "PUBLISHED"  # default

    def test_embedding_wrong_dimension_rejected(self):
        with pytest.raises(ValidationError, match="768-dimensional"):
            SearchRequest(query="test", embedding=[0.1, 0.2, 0.3])

    def test_embedding_correct_dimension_accepted(self):
        req = SearchRequest(query="test", embedding=[0.1] * 768)
        assert len(req.embedding) == 768

    def test_embedding_none_is_valid(self):
        req = SearchRequest(query="test", embedding=None)
        assert req.embedding is None

    def test_limit_bounds(self):
        with pytest.raises(ValidationError):
            SearchRequest(query="test", limit=0)
        with pytest.raises(ValidationError):
            SearchRequest(query="test", limit=101)
        # Boundary values should pass
        SearchRequest(query="test", limit=1)
        SearchRequest(query="test", limit=100)

    def test_query_max_length(self):
        with pytest.raises(ValidationError):
            SearchRequest(query="a" * 201)
        # Exactly at boundary should pass
        SearchRequest(query="a" * 200)

    def test_invalid_mode_rejected(self):
        with pytest.raises(ValidationError):
            SearchRequest(query="test", mode="invalid_mode")  # type: ignore[arg-type]

    def test_min_confidence_bounds(self):
        with pytest.raises(ValidationError):
            SearchRequest(query="test", min_confidence=1.5)
        with pytest.raises(ValidationError):
            SearchRequest(query="test", min_confidence=-0.1)


class TestSemanticSearchRequestValidation:
    def test_requires_exact_768_dims(self):
        with pytest.raises(ValidationError):
            SemanticSearchRequest(embedding=[0.1] * 100)

        # Exactly 768 should pass
        req = SemanticSearchRequest(embedding=[0.1] * 768)
        assert len(req.embedding) == 768


class TestUserContext:
    def test_valid_roles(self):
        for role in ["faculty", "coordinator", "hod", "admin", "system_worker"]:
            ctx = UserContext(user_id="u1", department_code="CSE", role=role)
            assert ctx.role == role

    def test_invalid_role_rejected(self):
        with pytest.raises(ValidationError):
            UserContext(user_id="u1", department_code="CSE", role="superadmin")  # type: ignore[arg-type]

    def test_is_admin_flag(self):
        ctx = UserContext(user_id="u1", department_code="CSE", role="admin", is_admin=True)
        assert ctx.is_admin is True
