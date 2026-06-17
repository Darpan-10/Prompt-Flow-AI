"""
Tier 1: Regex Extraction.
Extracts DOI and Year from raw text using exact locked patterns.
Confidence = 0.95 when DOI found, 0.60 for year-only.
"""
import re
import logging
from dataclasses import dataclass, field
from typing import Optional, List

logger = logging.getLogger(__name__)

# Locked DOI pattern from spec
_DOI_PATTERN = re.compile(
    r"\b(10\.\d{4,9}/[-._;()/:A-Z0-9a-z]+)\b"
)

# Year pattern — 4-digit year between 1900 and 2099
_YEAR_PATTERN = re.compile(
    r"\b(19[5-9]\d|20[0-2]\d)\b"
)

# Basic title heuristics — first non-empty line > 10 chars
_TITLE_LINE_MIN_LEN = 10


@dataclass
class RegexResult:
    doi: Optional[str]              = None
    year: Optional[int]             = None
    title_candidate: Optional[str]  = None
    authors_candidates: List[str]   = field(default_factory=list)
    doi_confidence: float           = 0.0
    year_confidence: float          = 0.0


def extract_with_regex(raw_text: str) -> RegexResult:
    """
    Tier 1 extraction using regex patterns.
    DOI confidence = 0.95 (exact pattern match).
    Year alone = 0.60 confidence on venue_year dimension.
    """
    result = RegexResult()

    # ── DOI ──────────────────────────────────────────────────────────────
    doi_match = _DOI_PATTERN.search(raw_text)
    if doi_match:
        result.doi = doi_match.group(1).rstrip(".")
        result.doi_confidence = 0.95
        logger.debug("Tier 1 DOI found: %s", result.doi)

    # ── Year ─────────────────────────────────────────────────────────────
    year_matches = _YEAR_PATTERN.findall(raw_text)
    if year_matches:
        # Take the most frequent year (publication year heuristic)
        from collections import Counter
        year_counts = Counter(int(y) for y in year_matches)
        result.year = year_counts.most_common(1)[0][0]
        result.year_confidence = 0.60
        logger.debug("Tier 1 Year found: %s", result.year)

    # ── Title candidate (first substantial line) ──────────────────────────
    lines = [l.strip() for l in raw_text.split("\n") if l.strip()]
    for line in lines[:10]:
        if len(line) > _TITLE_LINE_MIN_LEN and not line.startswith("http"):
            result.title_candidate = line[:300]
            break

    return result
