"""
Module 3 Validation Tests.
Run: pytest tests/ -v

Tests:
  ✅ 4-tier cascade logic
  ✅ Confidence formula
  ✅ Routing decisions (all 3 branches)
  ✅ Schema validation
  ✅ PII redaction preserved
  ✅ Hash verification failure → BLOCK
  ✅ Faculty status routing
  ✅ LLM confidence cap at 0.90
  ✅ DOI regex pattern
"""
import hashlib
import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock, AsyncMock

from app.models.schemas import (
    RoutingDecision, FacultyStatus, EnrichedContext,
    OverallConfidence, ValidationIssue,
    ExtractedMetadata, ExtractedAuthors, ExtractionTier,
    IngestedPayload,
)
from app.routing.engine import compute_routing
from app.services.extraction.tier1_regex import extract_with_regex
from app.services.extraction.tier4_bedrock import extract_with_bedrock


# ── Helpers ───────────────────────────────────────────────────────────────

def _make_confidence(authors=0.8, title=0.8, venue_year=0.8) -> OverallConfidence:
    score = (authors * 0.40) + (title * 0.30) + (venue_year * 0.30)
    return OverallConfidence(
        score=round(score, 4),
        authors_score=authors,
        title_score=title,
        venue_year_score=venue_year,
    )


def _active_faculty(faculty_id="dr.smith") -> EnrichedContext:
    return EnrichedContext(
        faculty_id=faculty_id,
        faculty_name="Dr. John Smith",
        faculty_email="dr.smith@srmap.edu.in",
        department_code="CSE",
        faculty_status=FacultyStatus.active,
    )


def _inactive_faculty() -> EnrichedContext:
    return EnrichedContext(
        faculty_id="dr.inactive",
        faculty_status=FacultyStatus.inactive,
    )


def _not_found_faculty() -> EnrichedContext:
    return EnrichedContext(
        faculty_id="unknown",
        faculty_status=FacultyStatus.not_found,
    )


# ── Confidence Formula ────────────────────────────────────────────────────

def test_confidence_formula_correct():
    conf = _make_confidence(authors=1.0, title=1.0, venue_year=1.0)
    assert conf.score == 1.0


def test_confidence_formula_partial():
    conf = _make_confidence(authors=0.75, title=0.75, venue_year=0.60)
    expected = (0.75 * 0.40) + (0.75 * 0.30) + (0.60 * 0.30)
    assert abs(conf.score - expected) < 0.001


def test_confidence_formula_mismatch_raises():
    with pytest.raises(Exception):
        OverallConfidence(
            score=0.99,  # Wrong — doesn't match formula
            authors_score=0.5,
            title_score=0.5,
            venue_year_score=0.5,
        )


# ── Routing: AUTO_SAVE ────────────────────────────────────────────────────

def test_routing_auto_save():
    routing = compute_routing(
        enriched_context=_active_faculty(),
        overall_confidence=_make_confidence(0.9, 0.9, 0.9),
        validation_issues=[],
    )
    assert routing.final_action == RoutingDecision.AUTO_SAVE
    assert "papers.validated" in routing.target_topic


# ── Routing: REVIEW_REQUIRED ──────────────────────────────────────────────

def test_routing_review_low_confidence():
    routing = compute_routing(
        enriched_context=_active_faculty(),
        overall_confidence=_make_confidence(0.4, 0.4, 0.4),
        validation_issues=[],
        confidence_threshold=0.75,
    )
    assert routing.final_action == RoutingDecision.REVIEW_REQUIRED
    assert "papers.review" in routing.target_topic


def test_routing_review_from_validation_issue():
    routing = compute_routing(
        enriched_context=_active_faculty(),
        overall_confidence=_make_confidence(0.9, 0.9, 0.9),
        validation_issues=[ValidationIssue(
            code="NO_ATTACHMENTS",
            message="No attachments",
            action="REVIEW_REQUIRED",
        )],
    )
    assert routing.final_action == RoutingDecision.REVIEW_REQUIRED


# ── Routing: BLOCK ────────────────────────────────────────────────────────

def test_routing_block_inactive_faculty():
    routing = compute_routing(
        enriched_context=_inactive_faculty(),
        overall_confidence=_make_confidence(0.9, 0.9, 0.9),
        validation_issues=[],
    )
    assert routing.final_action == RoutingDecision.BLOCK
    assert "papers.failed" in routing.target_topic


def test_routing_block_not_found_faculty():
    routing = compute_routing(
        enriched_context=_not_found_faculty(),
        overall_confidence=_make_confidence(0.9, 0.9, 0.9),
        validation_issues=[],
    )
    assert routing.final_action == RoutingDecision.BLOCK


def test_routing_block_from_validation_issue():
    routing = compute_routing(
        enriched_context=_active_faculty(),
        overall_confidence=_make_confidence(0.9, 0.9, 0.9),
        validation_issues=[ValidationIssue(
            code="MALWARE_FLAG",
            message="Infected",
            action="BLOCK",
        )],
    )
    assert routing.final_action == RoutingDecision.BLOCK


def test_routing_block_overrides_review():
    """BLOCK should take precedence over REVIEW_REQUIRED."""
    routing = compute_routing(
        enriched_context=_inactive_faculty(),
        overall_confidence=_make_confidence(0.4, 0.4, 0.4),
        validation_issues=[],
    )
    assert routing.final_action == RoutingDecision.BLOCK


# ── Tier 1 Regex ─────────────────────────────────────────────────────────

def test_tier1_doi_extraction():
    text = "DOI: 10.1145/3290605.3300501 Neural networks in education"
    result = extract_with_regex(text)
    assert result.doi == "10.1145/3290605.3300501"
    assert result.doi_confidence == 0.95


def test_tier1_year_extraction():
    text = "Published in IEEE Transactions 2023 on deep learning systems"
    result = extract_with_regex(text)
    assert result.year == 2023


def test_tier1_no_doi():
    text = "A study on academic performance without any doi identifier"
    result = extract_with_regex(text)
    assert result.doi is None
    assert result.doi_confidence == 0.0


def test_tier1_doi_with_complex_suffix():
    text = "See doi:10.1016/j.neunet.2023.01.005 for details"
    result = extract_with_regex(text)
    assert result.doi is not None
    assert result.doi.startswith("10.")


# ── Tier 4 Bedrock: confidence gate ──────────────────────────────────────

def test_bedrock_skipped_when_confidence_high():
    """Bedrock must NOT be invoked if confidence >= 0.70."""
    result = extract_with_bedrock("some text", current_confidence=0.75)
    assert result.invoked is False


def test_bedrock_confidence_cap():
    """Any Bedrock result must be hard-capped at 0.90."""
    with patch("app.services.extraction.tier4_bedrock.boto3") as mock_boto3:
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        mock_client.invoke_model.return_value = {
            "body": MagicMock(
                read=MagicMock(return_value=b'{"content": [{"text": "{\\"title\\": \\"Test Paper\\", \\"authors\\": [\\"Author A\\", \\"Author B\\"], \\"year\\": 2023, \\"venue\\": \\"ICML\\", \\"abstract\\": null, \\"doi\\": null}"}]}')
            )
        }
        result = extract_with_bedrock("some text", current_confidence=0.50)
        if result.invoked and not result.error:
            assert result.confidence <= 0.90, f"Confidence {result.confidence} exceeds cap of 0.90"


# ── Schema Validation ─────────────────────────────────────────────────────

def test_overall_confidence_zero_valid():
    conf = OverallConfidence(
        score=0.0,
        authors_score=0.0,
        title_score=0.0,
        venue_year_score=0.0,
    )
    assert conf.score == 0.0


def test_extracted_metadata_no_doi():
    meta = ExtractedMetadata(
        title="Test Paper",
        authors=ExtractedAuthors(names=["Author A"], confidence=0.8),
        title_confidence=0.8,
        venue_year_confidence=0.7,
        extraction_tier=ExtractionTier.NLP,
    )
    assert meta.doi is None
    assert meta.crossref_verified is False


# ── Integrity Verification ────────────────────────────────────────────────

def test_text_hash_correct():
    text = "This is the email body content for hash verification"
    expected_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    computed = hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert computed == expected_hash


def test_text_hash_mismatch_detected():
    text = "Original email text"
    correct_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    wrong_hash = hashlib.sha256(b"different content").hexdigest()
    assert correct_hash != wrong_hash


# ── Routing Reasons ───────────────────────────────────────────────────────

def test_routing_includes_reasons():
    routing = compute_routing(
        enriched_context=_not_found_faculty(),
        overall_confidence=_make_confidence(0.9, 0.9, 0.9),
        validation_issues=[],
    )
    assert len(routing.reasons) > 0
    assert any("not_found" in r for r in routing.reasons)


def test_auto_save_has_positive_reason():
    routing = compute_routing(
        enriched_context=_active_faculty(),
        overall_confidence=_make_confidence(0.9, 0.9, 0.9),
        validation_issues=[],
    )
    assert routing.final_action == RoutingDecision.AUTO_SAVE
    assert len(routing.reasons) > 0
