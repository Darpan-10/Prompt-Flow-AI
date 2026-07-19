"""
OAuth 2.0 Authentication endpoints.

Flow:
  GET  /auth/login     → redirect to Cognito Hosted UI
  GET  /auth/callback  → exchange code, issue internal JWT
  POST /auth/refresh   → rotate access token
  POST /auth/logout    → revoke + blocklist
  GET  /auth/me        → current user info
"""
import uuid
from typing import Optional
from fastapi import APIRouter, HTTPException, Request, Response, Depends
from fastapi.responses import RedirectResponse, JSONResponse

from app.config import settings
from app.services import cognito as cognito_svc
from app.services.audit import log_audit
from app.services.jwt_service import create_access_token, verify_token
from app.models.jwt import Role, AuthType, get_permissions, UserInfo
from app.models.audit import AuditAction, ResourceType
from app.dependencies import get_current_user
from app import state
import jwt as pyjwt

router = APIRouter()

REDIRECT_URI = "https://api.promptflow.ai/auth/callback"  # update per env


async def _lookup_provisioned_user(email: str) -> Optional[dict]:
    """
    Look up a user by email in Module 1's own users table.

    NAAC compliance requires accounts to be admin-provisioned, never
    auto-minted on login (see app/routes/users.py POST /). This means
    login can no longer trust Cognito's custom:role / custom:department_code
    attributes as authorization data — Cognito only verifies the person's
    identity (their email); role/department/faculty_id must come from the
    row an admin already created for them.

    Runs with an admin-context connection since, at this point in the
    flow, we don't yet know the caller's own department to satisfy the
    normal users_dept_isolation RLS policy — this is an internal system
    lookup by email, not a client request scoped to an authenticated user.
    """
    async with state.db_pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.current_role', 'admin', true)")
            row = await conn.fetchrow(
                """
                SELECT user_id, faculty_id, email, name, role, department_code, is_active
                FROM users
                WHERE email = $1
                """,
                email,
            )
    return dict(row) if row else None


@router.get("/login")
async def login(request: Request):
    """Redirect to Cognito Hosted UI for OAuth2 PKCE flow."""
    state_param = str(uuid.uuid4())
    # Store state in Redis (60s TTL) for CSRF protection
    await state.redis_client.setex(f"oauth_state:{state_param}", 60, "1")

    auth_url = (
        f"https://{settings.cognito_domain}/oauth2/authorize"
        f"?response_type=code"
        f"&client_id={settings.cognito_client_id}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&scope=openid+email+profile"
        f"&state={state_param}"
    )
    return RedirectResponse(url=auth_url)


@router.get("/callback")
async def auth_callback(request: Request, code: str, state: str):
    """
    Cognito redirects here after user authenticates.
    1. Validate CSRF state
    2. Exchange code for Cognito tokens
    3. Fetch user info
    4. Validate domain
    5. Issue internal JWT
    6. Set HttpOnly refresh token cookie
    """
    # CSRF check
    from app import state as app_state
    if not await app_state.redis_client.exists(f"oauth_state:{state}"):
        raise HTTPException(status_code=400, detail="Invalid or expired state parameter")
    await app_state.redis_client.delete(f"oauth_state:{state}")

    # Exchange code
    try:
        tokens = await cognito_svc.exchange_code_for_tokens(code, REDIRECT_URI)
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to exchange authorization code")

    # Fetch user info from Cognito
    try:
        user_info = await cognito_svc.get_user_info(tokens["access_token"])
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to fetch user info")

    email: str = user_info.get("email", "")
    domain = email.split("@")[-1] if "@" in email else ""

    if domain not in settings.allowed_email_domains:
        await log_audit(
            action=AuditAction.LOGIN_FAILED,
            actor_type="user",
            actor_id=user_info.get("sub"),
            resource_type=ResourceType.AUTH_SESSION,
            details={"reason": "domain_not_allowed", "email": email},
            request=request,
        )
        raise HTTPException(status_code=403, detail="domain_not_allowed")

    # Resolve role/department/faculty_id from Module 1's own users table —
    # never from Cognito custom attributes. Cognito only proves who the
    # person is (their verified email); it says nothing about whether an
    # admin has actually provisioned them, which role they hold today, or
    # their department. Trusting custom:role would let anyone who can
    # authenticate mint themselves any role by setting their own Cognito
    # attribute — the exact auto-mint-on-login gap NAAC provisioning rules
    # exist to close.
    provisioned = await _lookup_provisioned_user(email)
    if provisioned is None:
        await log_audit(
            action=AuditAction.LOGIN_FAILED,
            actor_type="user",
            actor_id=user_info.get("sub"),
            resource_type=ResourceType.AUTH_SESSION,
            details={"reason": "user_not_provisioned", "email": email},
            request=request,
        )
        raise HTTPException(status_code=403, detail="user_not_provisioned")

    if not provisioned["is_active"]:
        await log_audit(
            action=AuditAction.LOGIN_FAILED,
            actor_type="user",
            actor_id=provisioned["user_id"],
            resource_type=ResourceType.AUTH_SESSION,
            details={"reason": "account_deactivated", "email": email},
            request=request,
        )
        raise HTTPException(status_code=403, detail="account_deactivated")

    role = Role(provisioned["role"])
    dept_code = provisioned["department_code"]

    # Issue internal JWT
    access_token = create_access_token(
        sub=provisioned["user_id"],
        email=email,
        name=provisioned["name"] or user_info.get("name", email.split("@")[0]),
        role=role,
        department_code=dept_code,
        faculty_id=str(provisioned["faculty_id"]),
        auth_type=AuthType.user,
        trace_id=request.headers.get("x-trace-id"),
    )

    await log_audit(
        action=AuditAction.LOGIN_SUCCESS,
        actor_type="user",
        actor_id=provisioned["user_id"],
        resource_type=ResourceType.AUTH_SESSION,
        details={"email": email, "dept": dept_code, "role": role.value},
        request=request,
    )

    response = JSONResponse(content={
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": settings.access_token_expire_minutes * 60,
    })
    response.set_cookie(
        key="refresh_token",
        value=tokens.get("refresh_token", ""),
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=settings.refresh_token_expire_days * 86400,
    )
    return response


@router.post("/refresh")
async def refresh_token(request: Request):
    """Use refresh token cookie to issue a new access token."""
    refresh_tok = request.cookies.get("refresh_token")
    if not refresh_tok:
        raise HTTPException(status_code=401, detail="No refresh token")

    # Exchange with Cognito
    import httpx, base64
    token_url = f"https://{settings.cognito_domain}/oauth2/token"
    credentials = base64.b64encode(
        f"{settings.cognito_client_id}:{settings.cognito_client_secret}".encode()
    ).decode()

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            token_url,
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "refresh_token", "refresh_token": refresh_tok},
        )

    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    new_tokens = resp.json()
    user_info = await cognito_svc.get_user_info(new_tokens["access_token"])
    email = user_info["email"]

    provisioned = await _lookup_provisioned_user(email)
    if provisioned is None or not provisioned["is_active"]:
        raise HTTPException(status_code=403, detail="user_not_provisioned")

    role = Role(provisioned["role"])

    access_token = create_access_token(
        sub=provisioned["user_id"],
        email=email,
        name=provisioned["name"] or user_info.get("name", ""),
        role=role,
        department_code=provisioned["department_code"],
        faculty_id=str(provisioned["faculty_id"]),
        trace_id=request.headers.get("x-trace-id"),
    )

    await log_audit(
        AuditAction.TOKEN_REFRESHED,
        "user",
        provisioned["user_id"],
        resource_type=ResourceType.AUTH_SESSION,
        request=request,
    )

    return {"access_token": access_token, "token_type": "Bearer", "expires_in": 900}


@router.post("/logout")
async def logout(request: Request, claims: dict = Depends(get_current_user)):
    """Revoke refresh token and blocklist the current access token."""
    auth_header = request.headers.get("Authorization", "")
    access_token = auth_header.split(" ", 1)[1] if " " in auth_header else ""

    # Blocklist the access token until its natural expiry
    ttl = max(claims.get("exp", 0) - int(__import__("time").time()), 1)
    await state.redis_client.setex(f"blocklist:{access_token}", ttl, "1")

    # Revoke Cognito refresh token
    refresh_tok = request.cookies.get("refresh_token")
    if refresh_tok:
        await cognito_svc.revoke_token(refresh_tok)

    await log_audit(
        AuditAction.LOGOUT,
        "user",
        claims["sub"],
        resource_type=ResourceType.AUTH_SESSION,
        request=request,
    )

    response = JSONResponse(content={"message": "Logged out successfully"})
    response.delete_cookie("refresh_token")
    return response


@router.get("/me", response_model=UserInfo)
async def me(claims: dict = Depends(get_current_user)):
    """Return current user's claims and permissions."""
    return UserInfo(
        sub=claims["sub"],
        email=claims["email"],
        name=claims["name"],
        role=Role(claims["role"]),
        department_code=claims.get("department_code"),
        permissions=claims.get("permissions", []),
        faculty_id=claims.get("faculty_id"),
    )
