"""
Directory Service — Adapter Pattern.

Module 3 ONLY calls: await directory_service.get_faculty(faculty_id)
The adapter handles HTTP, retry, timeout, error mapping, and M2M auth.

Resilience:
  - Timeout: 3 seconds
  - Retries: 2 attempts
  - On failure: faculty_status = 'not_found' → BLOCK routing
"""
import base64
import logging
import time
from abc import ABC, abstractmethod
from typing import Optional

import httpx

from app.config import settings
from app.models.schemas import EnrichedContext, FacultyStatus

logger = logging.getLogger(__name__)

# Sentinel for "we don't have a real faculty_id" — used instead of the raw
# lookup key (e.g. "jdoe", not a UUID) when a faculty lookup comes back
# not_found/failed. faculty_status=not_found routes to papers.failed via
# the routing engine (see app/routing/engine.py), and Module 4 DOES
# consume papers.failed — its uuid.UUID(enriched_context.faculty_id) call
# would crash on a non-UUID string with no exception handling around it.
_UNRESOLVED_FACULTY_ID = "00000000-0000-0000-0000-000000000000"


# ── Interface ──────────────────────────────────────────────────────────────

class DirectoryServiceBase(ABC):
    @abstractmethod
    async def get_faculty(self, faculty_id: str) -> EnrichedContext:
        """Resolve faculty_id to enriched context. Never raises — returns not_found on error."""


# ── M2M token client (Module 1 issues these) ────────────────────────────────

class _M2MTokenClient:
    """
    Fetches and caches an M2M JWT from Module 1's /auth/m2m/token endpoint.
    Tokens are 15-min lived (see module1-auth create_m2m_token) — refetch
    a bit early to avoid racing expiry mid-request.
    """
    def __init__(self):
        self._token: Optional[str] = None
        self._expires_at: float = 0.0

    async def get_token(self) -> str:
        if self._token and time.monotonic() < self._expires_at:
            return self._token

        creds = f"{settings.m2m_client_id}:{settings.m2m_client_secret}".encode()
        auth_header = base64.b64encode(creds).decode()

        async with httpx.AsyncClient(timeout=settings.directory_timeout_seconds) as client:
            resp = await client.post(
                f"{settings.auth_service_url}/auth/m2m/token",
                headers={"Authorization": f"Basic {auth_header}"},
            )
        resp.raise_for_status()
        data = resp.json()

        self._token = data["access_token"]
        # Refresh 60s before actual expiry as a safety margin
        self._expires_at = time.monotonic() + max(data.get("expires_in", 900) - 60, 30)
        return self._token


_m2m_client = _M2MTokenClient()


# ── HTTP Adapter (Module 1's /api/faculty) ─────────────────────────────────

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
                token = await _m2m_client.get_token()
                async with httpx.AsyncClient(
                    timeout=settings.directory_timeout_seconds
                ) as client:
                    response = await client.get(
                        url, headers={"Authorization": f"Bearer {token}"}
                    )

                if response.status_code == 404:
                    logger.info(
                        "Directory: faculty_id '%s' not found (404)", faculty_id
                    )
                    return EnrichedContext(
                        faculty_id=_UNRESOLVED_FACULTY_ID,
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
            faculty_id=_UNRESOLVED_FACULTY_ID,
            faculty_status=FacultyStatus.not_found,
        )


def _map_response(lookup_key: str, data: dict) -> EnrichedContext:
    """
    Map API response dict to EnrichedContext schema.

    IMPORTANT: faculty_id here must be the real UUID Module 1 minted at
    provisioning (data["faculty_id"]), NOT the lookup_key used to find
    it (that's just the email local-part, e.g. "jdoe" — not a UUID).
    The old version of this function always echoed back lookup_key,
    which meant Module 4's `uuid.UUID(payload.enriched_context.faculty_id)`
    would crash on every single message, since "jdoe" isn't a valid
    UUID. Falls back to the lookup_key only if the response is missing
    the field, so a malformed response doesn't crash the whole pipeline.
    """
    raw_status = data.get("faculty_status", "not_found")
    try:
        status = FacultyStatus(raw_status)
    except ValueError:
        logger.warning("Unknown faculty_status '%s' — treating as not_found", raw_status)
        status = FacultyStatus.not_found

    return EnrichedContext(
        faculty_id=data.get("faculty_id", _UNRESOLVED_FACULTY_ID),
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
