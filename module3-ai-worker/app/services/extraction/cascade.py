"""
4-Tier Extraction Cascade Orchestrator.

Tier 1 (Regex)    → DOI + Year. Confidence = 0.95 if DOI found.
Tier 2 (CrossRef) → If DOI exists, overwrite all metadata. Confidence = 1.0.
Tier 3 (NLP)      → If no DOI, heuristic title/authors/venue. Confidence = 0.75.
Tier 4 (Bedrock)  → ONLY if overall_confidence < 0.70. Cap = 0.90.

Confidence formula: (authors*0.40) + (title*0.30) + (venue_year*0.30)
"""
import logging
from dataclasses import dataclass, field
from typing import Optional, List

from app.services.extraction.tier1_regex import extract_with_regex
from app.services.extraction.tier2_crossref import lookup_crossref
from app.services.extraction.tier3_nlp import extract_with_nlp
from app.services.extraction.tier4_bedrock import extract_with_bedrock
from app.models.schemas import (
    ExtractedMetadata,
    ExtractedAuthors,
    OverallConfidence,
    ExtractionTier,
)

logger = logging.getLogger(__name__)


def _compute_confidence(
    authors_score: float,
    title_score: float,
    venue_year_score: float,
) -> OverallConfidence:
    """Locked formula: (authors*0.40) + (title*0.30) + (venue_year*0.30)"""
    score = (authors_score * 0.40) + (title_score * 0.30) + (venue_year_score * 0.30)
    return OverallConfidence(
        score=round(score, 4),
        authors_score=authors_score,
        title_score=title_score,
        venue_year_score=venue_year_score,
    )


def run_extraction_cascade(raw_text: str) -> tuple[ExtractedMetadata, OverallConfidence]:
    """
    Execute the 4-tier extraction cascade.
    Returns (ExtractedMetadata, OverallConfidence).
    """

    # ── Tier 1: Regex ──────────────────────────────────────────────────────
    logger.info("Tier 1: Running regex extraction")
    t1 = extract_with_regex(raw_text)

    title               = t1.title_candidate
    authors: List[str]  = []
    doi                 = t1.doi
    year                = t1.year
    venue               = None
    abstract            = None
    crossref_verified   = False
    tier_used           = ExtractionTier.REGEX

    authors_score    = 0.0
    title_score      = 0.60 if title else 0.0
    venue_year_score = t1.year_confidence if year else 0.0

    # ── Tier 2: CrossRef ───────────────────────────────────────────────────
    if doi:
        logger.info("Tier 2: DOI found (%s) — querying CrossRef", doi)
        t2 = lookup_crossref(doi)

        if t2.found:
            # Overwrite ALL metadata with CrossRef authoritative data
            title              = t2.title or title
            authors            = t2.authors or authors
            year               = t2.year or year
            venue              = t2.venue
            abstract           = t2.abstract
            crossref_verified  = True
            tier_used          = ExtractionTier.CROSSREF

            # CrossRef = confidence 1.0 on all dimensions
            authors_score    = 1.0 if authors else 0.0
            title_score      = 1.0 if title else 0.0
            venue_year_score = 1.0 if (venue or year) else 0.0

            logger.info("Tier 2: CrossRef resolved — confidence 1.0")
        else:
            logger.info("Tier 2: CrossRef lookup failed — falling through to Tier 3")

    # ── Tier 3: NLP (only if no DOI or CrossRef failed) ───────────────────
    if not crossref_verified:
        logger.info("Tier 3: Running spaCy NLP extraction")
        t3 = extract_with_nlp(raw_text)

        if t3.title and not title:
            title = t3.title
            title_score = t3.title_confidence

        if t3.authors and not authors:
            authors = t3.authors
            authors_score = t3.authors_confidence

        if t3.venue and not venue:
            venue = t3.venue
            venue_year_score = max(venue_year_score, t3.venue_confidence)

        if t3.year and not year:
            year = t3.year

        tier_used = ExtractionTier.NLP

    # ── Compute confidence BEFORE potential Tier 4 ────────────────────────
    confidence = _compute_confidence(authors_score, title_score, venue_year_score)
    logger.info(
        "Pre-Tier4 confidence: %.4f (authors=%.2f, title=%.2f, venue_year=%.2f)",
        confidence.score, authors_score, title_score, venue_year_score,
    )

    # ── Tier 4: Bedrock LLM (only if confidence < 0.70) ───────────────────
    t4 = extract_with_bedrock(raw_text, current_confidence=confidence.score)

    if t4.invoked and not t4.error:
        tier_used = ExtractionTier.LLM

        if t4.title and not title:
            title = t4.title
            title_score = min(t4.confidence, 0.90)

        if t4.authors and not authors:
            authors = t4.authors
            authors_score = min(t4.confidence, 0.90)

        if t4.venue and not venue:
            venue = t4.venue
            venue_year_score = min(
                max(venue_year_score, t4.confidence), 0.90
            )

        if t4.year and not year:
            year = t4.year

        if t4.doi and not doi:
            doi = t4.doi

        if t4.abstract and not abstract:
            abstract = t4.abstract

        # Recompute confidence after Tier 4 enrichment
        confidence = _compute_confidence(authors_score, title_score, venue_year_score)

        # Hard cap at 0.90 after LLM
        if confidence.score > 0.90:
            capped = 0.90
            confidence = _compute_confidence(
                min(authors_score, 0.90),
                min(title_score, 0.90),
                min(venue_year_score, 0.90),
            )

        logger.info("Post-Tier4 confidence: %.4f (capped at 0.90)", confidence.score)

    # ── Assemble final ExtractedMetadata ──────────────────────────────────
    metadata = ExtractedMetadata(
        title=title,
        authors=ExtractedAuthors(
            names=authors,
            confidence=authors_score,
        ),
        doi=doi,
        year=year,
        venue=venue,
        abstract=abstract,
        title_confidence=title_score,
        venue_year_confidence=venue_year_score,
        extraction_tier=tier_used,
        crossref_verified=crossref_verified,
    )

    logger.info(
        "Extraction complete — tier=%s | doi=%s | confidence=%.4f",
        tier_used.value, doi, confidence.score,
    )

    return metadata, confidence
