"""
Module 6 – Auth Dependency
Extracts UserContext from JWTs issued by Module 1. Same trust model and
local-dev bypass pattern as Module 5's app/auth.py.
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
    try:
        if settings.SKIP_JWT_VALIDATION:
            return jwt.decode(token, options={"verify_signature": False})

        if not settings.JWT_PUBLIC_KEY:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="JWT_PUBLIC_KEY not configured and SKIP_JWT_VALIDATION is false",
            )
        return jwt.decode(
            token,
            settings.JWT_PUBLIC_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            audience=settings.JWT_AUDIENCE,
            issuer=settings.JWT_ISSUER,
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid token: {exc}")


async def get_current_user(authorization: Optional[str] = Header(None)) -> UserContext:
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

    return UserContext(
        user_id=user_id,
        department_code=department_code,
        role=role,
        faculty_id=claims.get("faculty_id"),
        is_admin=(role == "admin"),
    )


def authorize_report_request(user: UserContext, report_type: str, target_faculty_id: Optional[str]) -> None:
    """
    Authorization rules for report generation (no role check exists in
    the locked spec's output requirements, but generating compliance
    reports without ANY access control would be a real gap, not a
    faithful reading of "production-ready"):

    - NAAC_CRITERIA_III (department-wide): coordinator, hod, or admin only.
    - FACULTY_PROFILE: coordinator/hod/admin (on anyone's behalf), OR a
      faculty member generating their OWN profile.
    """
    if report_type == "NAAC_CRITERIA_III":
        if user.role not in ("coordinator", "hod", "admin"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="NAAC_CRITERIA_III reports require coordinator, hod, or admin role.",
            )
        return

    if report_type == "FACULTY_PROFILE":
        if user.role in ("coordinator", "hod", "admin"):
            return
        if user.role == "faculty" and target_faculty_id and str(user.faculty_id) == str(target_faculty_id):
            return
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="FACULTY_PROFILE reports require coordinator/hod/admin role, or the faculty member generating their own profile.",
        )
