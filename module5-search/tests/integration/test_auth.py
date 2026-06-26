"""
Module 5 – Integration Tests: Auth Dependency
Tests the get_current_user() FastAPI dependency end-to-end, including
the SKIP_JWT_VALIDATION local-dev bypass path.
"""

import uuid

import jwt
import pytest
from fastapi import HTTPException

from app.auth import get_current_user
from app.config import settings


def make_token(claims: dict) -> str:
    return jwt.encode(claims, key="test-key-irrelevant-when-unverified", algorithm="HS256")


class TestGetCurrentUser:
    @pytest.mark.asyncio
    async def test_valid_token_extracts_claims(self, monkeypatch):
        monkeypatch.setattr(settings, "SKIP_JWT_VALIDATION", True)
        user_id = str(uuid.uuid4())
        token = make_token({"sub": user_id, "department_code": "CSE", "role": "admin"})

        ctx = await get_current_user(authorization=f"Bearer {token}")

        assert ctx.user_id == user_id
        assert ctx.department_code == "CSE"
        assert ctx.role == "admin"
        assert ctx.is_admin is True

    @pytest.mark.asyncio
    async def test_faculty_role_extracts_faculty_id(self, monkeypatch):
        monkeypatch.setattr(settings, "SKIP_JWT_VALIDATION", True)
        faculty_id = str(uuid.uuid4())
        token = make_token({
            "sub": str(uuid.uuid4()),
            "department_code": "ECE",
            "role": "faculty",
            "faculty_id": faculty_id,
        })

        ctx = await get_current_user(authorization=f"Bearer {token}")

        assert ctx.role == "faculty"
        assert str(ctx.faculty_id) == faculty_id
        assert ctx.is_admin is False

    @pytest.mark.asyncio
    async def test_missing_authorization_header_raises_401(self, monkeypatch):
        monkeypatch.setattr(settings, "SKIP_JWT_VALIDATION", True)
        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(authorization=None)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_malformed_header_raises_401(self, monkeypatch):
        monkeypatch.setattr(settings, "SKIP_JWT_VALIDATION", True)
        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(authorization="NotBearer sometoken")
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_missing_required_claims_raises_401(self, monkeypatch):
        monkeypatch.setattr(settings, "SKIP_JWT_VALIDATION", True)
        # Missing department_code and role
        token = make_token({"sub": str(uuid.uuid4())})
        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(authorization=f"Bearer {token}")
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_each_role_parses_correctly(self, monkeypatch):
        monkeypatch.setattr(settings, "SKIP_JWT_VALIDATION", True)
        for role in ["faculty", "coordinator", "hod", "admin", "system_worker"]:
            token = make_token({
                "sub": str(uuid.uuid4()), "department_code": "CSE", "role": role,
            })
            ctx = await get_current_user(authorization=f"Bearer {token}")
            assert ctx.role == role
            assert ctx.is_admin == (role == "admin")
