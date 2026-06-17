"""
Module 1 Test Suite
Run: pytest tests/ -v --asyncio-mode=auto

Requires:
  - Running PostgreSQL + Redis (use docker-compose up -d for local)
  - .env.test with test DB credentials
  - pytest, pytest-asyncio, httpx
"""
import pytest
import time
import jwt
from pathlib import Path
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch, MagicMock

from app.main import app
from app.services.jwt_service import create_access_token, create_m2m_token, verify_token
from app.models.jwt import Role, AuthType

client = TestClient(app)


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def make_token(
    sub="user_001",
    email="test@srmap.edu.in",
    name="Test User",
    role=Role.faculty,
    dept="CSE",
):
    return create_access_token(
        sub=sub, email=email, name=name,
        role=role, department_code=dept,
    )


def auth_header(token: str):
    return {"Authorization": f"Bearer {token}"}


# ─────────────────────────────────────────────
# 1. Health Check
# ─────────────────────────────────────────────

def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ─────────────────────────────────────────────
# 2. JWT — create and verify
# ─────────────────────────────────────────────

def test_create_and_verify_access_token():
    token = make_token()
    claims = verify_token(token)
    assert claims["sub"] == "user_001"
    assert claims["email"] == "test@srmap.edu.in"
    assert claims["role"] == "faculty"
    assert claims["department_code"] == "CSE"
    assert "paper.view.self" in claims["permissions"]
    assert claims["auth_type"] == "user"


def test_m2m_token_claims():
    token = create_m2m_token("worker_module2")
    claims = verify_token(token)
    assert claims["role"] == "system_worker"
    assert claims["auth_type"] == "m2m"
    assert "queue.consume" in claims["permissions"]
    assert "db.write.internal" in claims["permissions"]
    # M2M should NOT have user-level permissions
    assert "paper.view.self" not in claims["permissions"]
    assert "user.delete" not in claims["permissions"]


def test_expired_token_rejected():
    """Manually craft an expired token."""
    private_key = Path("keys/private.pem").read_text()
    payload = {
        "sub": "user_expired",
        "email": "exp@srmap.edu.in",
        "name": "Expired",
        "role": "faculty",
        "department_code": "CSE",
        "permissions": [],
        "iss": "https://auth.promptflow.ai",
        "exp": int(time.time()) - 100,  # already expired
        "iat": int(time.time()) - 200,
        "auth_type": "user",
        "trace_id": "test-trace",
    }
    expired_token = jwt.encode(payload, private_key, algorithm="RS256")
    with pytest.raises(jwt.ExpiredSignatureError):
        verify_token(expired_token)


def test_tampered_token_rejected():
    """Modify a valid token payload — signature should fail."""
    token = make_token()
    parts = token.split(".")
    # Flip last char of payload to corrupt it
    corrupted = parts[0] + "." + parts[1][:-1] + ("A" if parts[1][-1] != "A" else "B") + "." + parts[2]
    with pytest.raises(jwt.PyJWTError):
        verify_token(corrupted)


# ─────────────────────────────────────────────
# 3. GET /auth/me
# ─────────────────────────────────────────────

def test_me_endpoint_valid_token():
    token = make_token(role=Role.coordinator, dept="ECE")
    with patch("app.state.redis_client") as mock_redis:
        mock_redis.exists = AsyncMock(return_value=0)
        resp = client.get("/auth/me", headers=auth_header(token))
    assert resp.status_code == 200
    data = resp.json()
    assert data["role"] == "coordinator"
    assert data["department_code"] == "ECE"


def test_me_endpoint_missing_token():
    resp = client.get("/auth/me")
    assert resp.status_code == 401


def test_me_endpoint_invalid_token():
    resp = client.get("/auth/me", headers=auth_header("not.a.real.token"))
    assert resp.status_code == 401


def test_me_endpoint_wrong_domain():
    """Token with non-srmap domain should be rejected."""
    token = create_access_token(
        sub="x", email="hacker@gmail.com", name="Hacker",
        role=Role.faculty, department_code="CSE",
    )
    with patch("app.state.redis_client") as mock_redis:
        mock_redis.exists = AsyncMock(return_value=0)
        resp = client.get("/auth/me", headers=auth_header(token))
    assert resp.status_code == 403
    assert resp.json()["detail"] == "domain_not_allowed"


# ─────────────────────────────────────────────
# 4. Role Permission Checks
# ─────────────────────────────────────────────

def test_faculty_permissions_correct():
    token = make_token(role=Role.faculty)
    claims = verify_token(token)
    perms = claims["permissions"]
    assert "paper.submit" in perms
    assert "paper.view.self" in perms
    # Faculty must NOT have admin permissions
    assert "user.delete" not in perms
    assert "system.config" not in perms
    assert "export.global" not in perms


def test_admin_permissions_correct():
    token = make_token(role=Role.admin, dept=None)
    claims = verify_token(token)
    perms = claims["permissions"]
    assert "user.delete" in perms
    assert "system.config" in perms
    assert "export.global" in perms
    assert "audit.view" in perms


def test_hod_permissions_correct():
    token = make_token(role=Role.hod)
    claims = verify_token(token)
    perms = claims["permissions"]
    assert "user.manage" in perms
    assert "audit.view" in perms
    assert "metrics.view" in perms
    # HOD must NOT have global admin perms
    assert "user.delete" not in perms
    assert "system.config" not in perms


# ─────────────────────────────────────────────
# 5. Blocklisted token (logout simulation)
# ─────────────────────────────────────────────

def test_blocklisted_token_rejected():
    token = make_token()
    with patch("app.state.redis_client") as mock_redis:
        mock_redis.exists = AsyncMock(return_value=1)  # token IS in blocklist
        resp = client.get("/auth/me", headers=auth_header(token))
    assert resp.status_code == 401
    assert "revoked" in resp.json()["detail"].lower()


# ─────────────────────────────────────────────
# 6. Rate Limiting
# ─────────────────────────────────────────────

def test_rate_limit_triggers_after_threshold():
    """After exceeding rate limit, /auth/login should return 429."""
    with patch("app.state.redis_client") as mock_redis:
        # Simulate 6 attempts already in the window (limit is 5)
        mock_redis.pipeline = MagicMock(return_value=AsyncMock(
            execute=AsyncMock(return_value=[None, None, 6, None])
        ))
        resp = client.get("/auth/login")
    assert resp.status_code == 429
    assert resp.json()["error"] == "too_many_attempts"


# ─────────────────────────────────────────────
# 7. M2M endpoint
# ─────────────────────────────────────────────

def test_m2m_token_endpoint_invalid_credentials():
    with patch("app.routes.m2m.verify_m2m_client", new=AsyncMock(return_value=False)):
        with patch("app.state.db_pool") as mock_pool:
            mock_pool.acquire = MagicMock(return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=AsyncMock(execute=AsyncMock())),
                __aexit__=AsyncMock(return_value=False),
            ))
            resp = client.post(
                "/auth/m2m/token",
                auth=("bad_client", "bad_secret"),
            )
    assert resp.status_code == 401


def test_m2m_token_endpoint_valid_credentials():
    with patch("app.routes.m2m.verify_m2m_client", new=AsyncMock(return_value=True)):
        with patch("app.services.audit.log_audit", new=AsyncMock()):
            resp = client.post(
                "/auth/m2m/token",
                auth=("worker_module2", "correct_secret"),
            )
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["expires_in"] == 900
    assert "queue.consume" in data["scope"]


# ─────────────────────────────────────────────
# 8. Audit log immutability (DB-level)
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_audit_log_no_update_permission():
    """
    This test requires a real DB connection.
    Verifies that UPDATE on audit_log raises InsufficientPrivilegeError.
    Skip if no DB available.
    """
    import asyncpg
    import os
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        pytest.skip("DATABASE_URL not set")

    conn = await asyncpg.connect(db_url)
    try:
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await conn.execute("UPDATE audit_log SET details = '{}' WHERE log_id = gen_random_uuid()")
    finally:
        await conn.close()

import app.state as state

class MockPipeline:
    def zremrangebyscore(self, *args, **kwargs):
        return self

    def zadd(self, *args, **kwargs):
        return self

    def zcard(self, *args, **kwargs):
        return self

    def expire(self, *args, **kwargs):
        return self

    async def execute(self):
        return [None, None, 1, None]


class MockRedis:
    def __init__(self):
        self.store = {}

    async def exists(self, key):
        return 0

    def pipeline(self):
        return MockPipeline()
# Apply before tests
state.redis_client = MockRedis()
