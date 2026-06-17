"""
Tier 3: spaCy NLP Heuristic Extraction.
Used when no DOI is found (Tier 1/2 insufficient).
Extracts title, authors, venue using NLP Named Entity Recognition.
Confidence = 0.75 on successful extraction.
"""
import re
import logging
from dataclasses import dataclass, field
from typing import Optional, List

logger = logging.getLogger(__name__)

# Lazy-load spaCy to avoid cold-start delay if not needed
_nlp = None


def _get_nlp():
    global _nlp
    if _nlp is None:
        try:
            import spacy
            _nlp = spacy.load("en_core_web_sm")
        except OSError:
            logger.error(
                "spaCy model 'en_core_web_sm' not found. "
                "Run: python -m spacy download en_core_web_sm"
            )
            raise
    return _nlp


# Patterns for academic paper heuristics
_AUTHOR_LINE_PATTERN = re.compile(
    r"^([A-Z][a-z]+(?:\s[A-Z]\.?\s?)?[A-Z][a-z]+)"
    r"(?:[,\s]+[A-Z][a-z]+(?:\s[A-Z]\.?\s?)?[A-Z][a-z]+)*$"
)

_VENUE_KEYWORDS = [
    "conference", "journal", "proceedings", "workshop",
    "symposium", "transactions", "letters", "review", "arxiv"
]

_YEAR_PATTERN = re.compile(r"\b(19[5-9]\d|20[0-2]\d)\b")


@dataclass
class NLPResult:
    title: Optional[str]        = None
    authors: List[str]          = field(default_factory=list)
    venue: Optional[str]        = None
    year: Optional[int]         = None
    title_confidence: float     = 0.0
    authors_confidence: float   = 0.0
    venue_confidence: float     = 0.0


def extract_with_nlp(raw_text: str) -> NLPResult:
    """
    Tier 3: NLP extraction using spaCy.
    Confidence = 0.75 when title + authors found.
    """
    result = NLPResult()
    nlp = _get_nlp()

    # Use only the first 3000 chars (header section of paper)
    header_text = raw_text[:3000]

    # ── Title: first substantial line heuristic ────────────────────────────
    lines = [l.strip() for l in header_text.split("\n") if l.strip()]
    for line in lines[:15]:
        # Title lines: title-case, length 20-200, no URLs
        if (
            20 <= len(line) <= 200
            and not line.startswith("http")
            and not line[0].isdigit()
            and sum(1 for c in line if c.isupper()) >= 2
        ):
            result.title = line
            result.title_confidence = 0.75
            break

    # ── Authors: NLP PERSON entities ──────────────────────────────────────
    doc = nlp(header_text)
    persons = list({ent.text.strip() for ent in doc.ents if ent.label_ == "PERSON"})

    # Filter out single-word entries and very long strings
    authors = [
        p for p in persons
        if 3 <= len(p) <= 60 and " " in p
    ][:8]  # Cap at 8 authors

    if authors:
        result.authors = authors
        result.authors_confidence = 0.75

    # ── Venue: ORG entities containing venue keywords ─────────────────────
    orgs = [ent.text.strip() for ent in doc.ents if ent.label_ == "ORG"]
    for org in orgs:
        if any(kw in org.lower() for kw in _VENUE_KEYWORDS):
            result.venue = org[:200]
            result.venue_confidence = 0.75
            break

    # ── Year: from DATE entities ───────────────────────────────────────────
    for ent in doc.ents:
        if ent.label_ == "DATE":
            year_match = _YEAR_PATTERN.search(ent.text)
            if year_match:
                result.year = int(year_match.group(1))
                break

    logger.info(
        "Tier 3 NLP: title=%s | authors=%d | venue=%s | year=%s",
        bool(result.title), len(result.authors), bool(result.venue), result.year,
    )

    return result
