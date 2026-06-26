"""
Module 5 – Auth Dependency
Extracts UserContext (department_code, role, user_id) from JWTs issued by
Module 1's Auth Service. Module 5 TRUSTS these claims -- it does not
independently verify a user's department membership, only the JWT's
cryptographic signature (or skips verification entirely in local dev).

Local testing: set SKIP_JWT_VALIDATION=true in .env, then pass headers
directly (see SETUP.md for the exact curl examples / mock-token script).
"""

from __future__ import annotations

import logging
from typing import Optional

import jwt
from fastapi import Header, HTTPException, status

from app.config import settings
from app.schemas import UserContext

log = logging.getLogger(__name__)


def _decode_jwt(token: str) -> dict:
    """
    Decode and verify a JWT issued by Module 1.

    In production (SKIP_JWT_VALIDATION=false), this verifies the RS256
    signature against Module 1's public key (JWT_PUBLIC_KEY env var) and
    checks issuer/audience. In local dev, signature verification can be
    skipped entirely so you can hand-craft test tokens without needing
    Module 1's private key.
    """
    if settings.SKIP_JWT_VALIDATION:
        # Decode WITHOUT verifying signature -- local/dev testing only.
        # Still parses standard claims so the rest of the pipeline behaves
        # identically to production.
        return jwt.decode(token, options={"verify_signature": False})

    public_key = getattr(settings, "JWT_PUBLIC_KEY", None)
    if not public_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="JWT_PUBLIC_KEY not configured and SKIP_JWT_VALIDATION is false",
        )
    try:
        return jwt.decode(
            token,
            public_key,
            algorithms=[settings.JWT_ALGORITHM],
            audience=settings.JWT_AUDIENCE,
            issuer=settings.JWT_ISSUER,
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid token: {exc}")


async def get_current_user(
    authorization: Optional[str] = Header(None),
) -> UserContext:
    """
    FastAPI dependency: extracts UserContext from the Authorization header.

    Expected header: "Authorization: Bearer <jwt>"

    Expected JWT claims (issued by Module 1):
      - sub: user_id (UUID string)
      - department_code: e.g. "CSE"
      - role: one of faculty|coordinator|hod|admin|system_worker
      - faculty_id: UUID string, present only if role == "faculty"
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header. Expected: 'Bearer <jwt>'",
        )

    token = authorization.removeprefix("Bearer ").strip()
    claims = _decode_jwt(token)

    user_id = claims.get("sub")
    department_code = claims.get("department_code")
    role = claims.get("role")

    if not user_id or not department_code or not role:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="JWT missing required claims: sub, department_code, role",
        )

    faculty_id = claims.get("faculty_id")

    return UserContext(
        user_id=user_id,
        department_code=department_code,
        role=role,
        faculty_id=faculty_id,
        is_admin=(role == "admin"),
    )
