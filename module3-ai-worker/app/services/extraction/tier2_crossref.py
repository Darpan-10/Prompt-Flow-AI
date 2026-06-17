"""
Tier 2: CrossRef API Lookup.
If DOI found in Tier 1, query api.crossref.org to get authoritative metadata.
Confidence = 1.0 on all fields when CrossRef responds successfully.
"""
import logging
from dataclasses import dataclass, field
from typing import Optional, List

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class CrossRefResult:
    found: bool                     = False
    title: Optional[str]            = None
    authors: List[str]              = field(default_factory=list)
    year: Optional[int]             = None
    venue: Optional[str]            = None
    abstract: Optional[str]         = None
    doi: Optional[str]              = None
    publisher: Optional[str]        = None
    confidence: float               = 0.0


def _parse_author(author: dict) -> str:
    given = author.get("given", "")
    family = author.get("family", "")
    return f"{given} {family}".strip() if given or family else ""


def lookup_crossref(doi: str) -> CrossRefResult:
    """
    Query CrossRef API for metadata by DOI.
    Returns confidence=1.0 on success.
    Uses polite pool (mailto header) per CrossRef etiquette.
    """
    url = f"{settings.crossref_api_url}/{doi}"
    headers = {
        "User-Agent": f"PromptFlowAI/1.0 (mailto:{settings.crossref_mailto})",
    }

    try:
        with httpx.Client(timeout=settings.crossref_timeout_seconds) as client:
            response = client.get(url, headers=headers)

        if response.status_code == 404:
            logger.info("CrossRef: DOI not found: %s", doi)
            return CrossRefResult(found=False)

        if response.status_code != 200:
            logger.warning(
                "CrossRef: Unexpected status %d for DOI %s",
                response.status_code, doi,
            )
            return CrossRefResult(found=False)

        data = response.json().get("message", {})

        # Extract title
        titles = data.get("title", [])
        title = titles[0] if titles else None

        # Extract authors
        authors_raw = data.get("author", [])
        authors = [a for a in (_parse_author(a) for a in authors_raw) if a]

        # Extract year from published-print or published-online
        year = None
        for date_field in ("published-print", "published-online", "created"):
            date_parts = data.get(date_field, {}).get("date-parts", [[]])
            if date_parts and date_parts[0]:
                year = date_parts[0][0]
                break

        # Extract venue
        venue_parts = data.get("container-title", [])
        venue = venue_parts[0] if venue_parts else data.get("publisher")

        # Extract abstract
        abstract = data.get("abstract", "").replace("<jats:p>", "").replace("</jats:p>", "").strip() or None

        logger.info(
            "CrossRef: DOI resolved — title: %s | authors: %d | year: %s",
            title, len(authors), year,
        )

        return CrossRefResult(
            found=True,
            title=title,
            authors=authors,
            year=year,
            venue=venue,
            abstract=abstract,
            doi=doi,
            publisher=data.get("publisher"),
            confidence=1.0,
        )

    except httpx.TimeoutException:
        logger.warning("CrossRef: Timeout for DOI %s", doi)
        return CrossRefResult(found=False)
    except Exception as e:
        logger.error("CrossRef: Error for DOI %s: %s", doi, str(e))
        return CrossRefResult(found=False)
