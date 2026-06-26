"""
Module 5 – Unit Tests: Query Sanitization & Cursor Pagination
"""

import uuid
from datetime import datetime, timezone

import pytest

from app.config import settings
from app.schemas import Author, Cursor, SearchResult
from app.services.search_service import sanitize_query


class TestSanitizeQuery:
    def test_empty_string(self):
        assert sanitize_query("") == ""

    def test_none_like_empty(self):
        assert sanitize_query(None) == ""  # type: ignore[arg-type]

    def test_strips_control_characters(self):
        # Control characters are replaced with a space (not deleted outright)
        # so that word boundaries are preserved -- e.g. a literal newline
        # between two words must not merge them together.
        dirty = "hello\x00world\x1f!"
        assert sanitize_query(dirty) == "hello world !"

    def test_strips_semicolons(self):
        dirty = "robert'); DROP TABLE papers;--"
        cleaned = sanitize_query(dirty)
        assert ";" not in cleaned

    def test_collapses_whitespace(self):
        dirty = "machine    learning\n\nin   healthcare"
        assert sanitize_query(dirty) == "machine learning in healthcare"

    def test_truncates_to_max_length(self):
        long_query = "a" * 500
        cleaned = sanitize_query(long_query)
        assert len(cleaned) == settings.QUERY_MAX_LENGTH

    def test_strips_leading_trailing_whitespace(self):
        assert sanitize_query("   attention is all you need   ") == "attention is all you need"

    def test_normal_query_unchanged(self):
        normal = "transformer neural network architecture"
        assert sanitize_query(normal) == normal


class TestCursor:
    def test_encode_decode_roundtrip(self):
        result = SearchResult(
            paper_id=uuid.uuid4(),
            title="Test Paper",
            authors=[Author(name="Dr. Test")],
            venue="Test Venue",
            year=2024,
            doi="10.1234/test",
            paper_type="journal",
            status="PUBLISHED",
            overall_confidence=0.9,
            created_at=datetime.now(timezone.utc),
            relevance_score=0.85,
            search_mode="keyword",
        )

        encoded = Cursor.from_result(result)
        assert isinstance(encoded, str)

        decoded = Cursor.decode(encoded)
        assert decoded.paper_id == result.paper_id
        # Compare timestamps with second precision (isoformat roundtrip)
        assert decoded.created_at.replace(microsecond=0) == result.created_at.replace(
            microsecond=0, tzinfo=decoded.created_at.tzinfo
        )

    def test_decode_invalid_cursor_raises(self):
        with pytest.raises(Exception):
            Cursor.decode("not-valid-base64-json!!!")

    def test_different_results_produce_different_cursors(self):
        r1 = SearchResult(
            paper_id=uuid.uuid4(),
            title="Paper 1",
            authors=[Author(name="A")],
            venue=None,
            year=2024,
            doi=None,
            paper_type="journal",
            status="PUBLISHED",
            overall_confidence=0.8,
            created_at=datetime.now(timezone.utc),
            relevance_score=0.5,
            search_mode="keyword",
        )
        r2 = SearchResult(
            paper_id=uuid.uuid4(),
            title="Paper 2",
            authors=[Author(name="B")],
            venue=None,
            year=2024,
            doi=None,
            paper_type="journal",
            status="PUBLISHED",
            overall_confidence=0.8,
            created_at=datetime.now(timezone.utc),
            relevance_score=0.5,
            search_mode="keyword",
        )

        assert Cursor.from_result(r1) != Cursor.from_result(r2)
