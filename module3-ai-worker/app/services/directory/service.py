"""
Directory Service — Adapter Pattern.

Module 3 ONLY calls: await directory_service.get_faculty(faculty_id)
The adapter handles HTTP, retry, timeout, and error mapping.

Resilience:
  - Timeout: 3 seconds
  - Retries: 2 attempts
  - On failure: faculty_status = 'not_found' → BLOCK routing
"""
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

import httpx

from app.config import settings
from app.models.schemas import EnrichedContext, FacultyStatus

logger = logging.getLogger(__name__)


# ── Interface ──────────────────────────────────────────────────────────────

class DirectoryServiceBase(ABC):
    @abstractmethod
    async def get_faculty(self, faculty_id: str) -> EnrichedContext:
        """Resolve faculty_id to enriched context. Never raises — returns not_found on error."""


# ── HTTP Adapter (production + mock) ─────────────────────────────────────

class HTTPDirectoryService(DirectoryServiceBase):
    """
    Calls DIRECTORY_API_URL/api/faculty/{faculty_id}
    Maps response to EnrichedContext.
    Falls back to not_found on any error.
    """

    def __init__(self, base_url: Optional[str] = None):
        self.base_url = (base_url or settings.directory_api_url).rstrip("/")

    async def get_faculty(self, faculty_id: str) -> EnrichedContext:
        url = f"{self.base_url}/api/faculty/{faculty_id}"
        last_error = None

        for attempt in range(1, settings.directory_max_retries + 1):
            try:
                async with httpx.AsyncClient(
                    timeout=settings.directory_timeout_seconds
                ) as client:
                    response = await client.get(url)

                if response.status_code == 404:
                    logger.info(
                        "Directory: faculty_id '%s' not found (404)", faculty_id
                    )
                    return EnrichedContext(
                        faculty_id=faculty_id,
                        faculty_status=FacultyStatus.not_found,
                    )

                if response.status_code == 200:
                    data = response.json()
                    return _map_response(faculty_id, data)

                logger.warning(
                    "Directory: Unexpected status %d for faculty_id '%s' (attempt %d/%d)",
                    response.status_code, faculty_id,
                    attempt, settings.directory_max_retries,
                )
                last_error = f"HTTP {response.status_code}"

            except httpx.TimeoutException:
                last_error = "timeout"
                logger.warning(
                    "Directory: Timeout for faculty_id '%s' (attempt %d/%d)",
                    faculty_id, attempt, settings.directory_max_retries,
                )
            except Exception as e:
                last_error = str(e)
                logger.error(
                    "Directory: Error for faculty_id '%s' (attempt %d/%d): %s",
                    faculty_id, attempt, settings.directory_max_retries, str(e),
                )

        logger.error(
            "Directory: All %d attempts failed for faculty_id '%s' — last error: %s. "
            "Treating as not_found.",
            settings.directory_max_retries, faculty_id, last_error,
        )
        return EnrichedContext(
            faculty_id=faculty_id,
            faculty_status=FacultyStatus.not_found,
        )


def _map_response(faculty_id: str, data: dict) -> EnrichedContext:
    """Map API response dict exactly to EnrichedContext schema."""
    raw_status = data.get("faculty_status", "not_found")
    try:
        status = FacultyStatus(raw_status)
    except ValueError:
        logger.warning("Unknown faculty_status '%s' — treating as not_found", raw_status)
        status = FacultyStatus.not_found

    return EnrichedContext(
        faculty_id=faculty_id,
        faculty_name=data.get("faculty_name"),
        faculty_email=data.get("faculty_email"),
        department_code=data.get("department_code"),
        faculty_status=status,
    )


# ── Singleton factory ──────────────────────────────────────────────────────

_directory_service: Optional[HTTPDirectoryService] = None


def get_directory_service() -> HTTPDirectoryService:
    global _directory_service
    if _directory_service is None:
        _directory_service = HTTPDirectoryService()
    return _directory_service
