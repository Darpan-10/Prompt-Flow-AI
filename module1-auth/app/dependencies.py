"""
FastAPI dependencies for:
- JWT validation (get_current_user)
- Role-based access guards (require_role)
- PostgreSQL RLS context injection (get_db_session)
"""
import jwt as pyjwt
from fastapi import Depends, HTTPException, Request
from typing import Optional, List
from app import state
from app.config import settings
from app.services.jwt_service import verify_token
from app.models.jwt import Role


async def get_current_user(request: Request) -> dict:
    """
    Validates Bearer token, checks Redis blocklist, enforces domain allowlist.
    Returns decoded JWT claims dict.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token")

    token = auth_header.split(" ", 1)[1]

    # Check blocklist (logged-out tokens)
    if await state.redis_client.exists(f"blocklist:{token}"):
        raise HTTPException(status_code=401, detail="Token has been revoked")

    try:
        claims = verify_token(token)
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except pyjwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    # Domain allowlist
    email: str = claims.get("email", "")
    domain = email.split("@")[-1] if "@" in email else ""
    if domain not in settings.allowed_email_domains and claims.get("auth_type") != "m2m":
        raise HTTPException(status_code=403, detail="domain_not_allowed")

    return claims


def require_role(*roles: Role):
    """Dependency factory: enforces that current user has one of the given roles."""
    async def _check(claims: dict = Depends(get_current_user)):
        if claims.get("role") not in [r.value for r in roles]:
            raise HTTPException(status_code=403, detail="insufficient_permissions")
        return claims
    return _check


def require_permission(permission: str):
    """Dependency factory: enforces a specific permission string."""
    async def _check(claims: dict = Depends(get_current_user)):
        if permission not in claims.get("permissions", []):
            raise HTTPException(status_code=403, detail="insufficient_permissions")
        return claims
    return _check


class DBSession:
    """
    Async context manager that acquires a DB connection,
    injects RLS session variables, and cleans up on exit.
    """
    def __init__(self, claims: dict):
        self.claims = claims
        self.conn = None

    async def __aenter__(self):
        self.conn = await state.db_pool.acquire()
        c = self.claims
        if c.get("department_code"):
            await self.conn.execute(
                f"SET LOCAL app.current_department = '{c['department_code']}'"
            )
        await self.conn.execute(f"SET LOCAL app.current_role = '{c['role']}'")
        await self.conn.execute(f"SET LOCAL app.current_user_id = '{c['sub']}'")
        return self.conn

    async def __aexit__(self, *_):
        await self.conn.execute("RESET ALL")
        await state.db_pool.release(self.conn)


async def get_db_session(claims: dict = Depends(get_current_user)):
    """Yields a DB connection with RLS context injected."""
    async with DBSession(claims) as conn:
        yield conn
