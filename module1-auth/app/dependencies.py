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
    Async context manager that acquires a DB connection, opens a
    transaction, injects RLS session variables, and cleans up on exit.

    Two bugs fixed here (same root causes as the ones found while
    building Module 4/5/6 — see their app/database.py):

    1. Connection leak: the old version called state.db_pool.acquire()
       and only released the connection in __aexit__. If anything
       between acquire() and return raised (including a bad SET LOCAL),
       __aexit__ never ran and the connection was leaked back to the
       pool forever. Now acquire + context injection is wrapped in its
       own try/except so the connection is always released.

    2. SET LOCAL via f-string interpolation: `SET LOCAL app.x = '{value}'`
       is both a SQL-injection-shaped string build AND functionally
       broken for the same reason `SET LOCAL ... = $1` is — Postgres's
       SET is a utility statement and doesn't take bind parameters, but
       building it via untrusted string interpolation is exactly the
       injection risk parameterized queries exist to prevent. The fix
       used across Modules 4/5/6 is set_config(name, value, true): a
       normal function call, so it accepts bind parameters safely, and
       the third arg (is_local=true) reproduces SET LOCAL's
       transaction-scoped behavior. That scoping only works if the
       injection and the queries that depend on it share one
       transaction — so unlike the old version (which ran each
       statement as its own implicit auto-committed transaction and
       would have silently dropped the RLS context before the caller's
       first real query), this version opens one explicit transaction
       for the life of the request and commits/rolls back in __aexit__.
    """
    def __init__(self, claims: dict):
        self.claims = claims
        self.conn = None
        self._tx = None

    async def __aenter__(self):
        self.conn = await state.db_pool.acquire()
        try:
            self._tx = self.conn.transaction()
            await self._tx.start()

            c = self.claims
            await self.conn.execute(
                "SELECT set_config('app.current_department', $1, true)",
                c.get("department_code") or "",
            )
            await self.conn.execute(
                "SELECT set_config('app.current_role', $1, true)",
                c.get("role", ""),
            )
            await self.conn.execute(
                "SELECT set_config('app.current_user_id', $1, true)",
                c.get("sub", ""),
            )
            return self.conn
        except Exception:
            if self._tx is not None:
                await self._tx.rollback()
            await state.db_pool.release(self.conn)
            self.conn = None
            raise

    async def __aexit__(self, exc_type, exc, tb):
        try:
            if self.conn is None:
                return
            if exc_type is not None:
                await self._tx.rollback()
            else:
                await self._tx.commit()
        finally:
            if self.conn is not None:
                await state.db_pool.release(self.conn)


async def get_db_session(claims: dict = Depends(get_current_user)):
    """Yields a DB connection with RLS context injected."""
    async with DBSession(claims) as conn:
        yield conn
