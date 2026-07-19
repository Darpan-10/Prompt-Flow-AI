"""
User management endpoints.
Only HOD and Admin can access these.
"""
import uuid
from fastapi import APIRouter, Depends, HTTPException, Request
from app.dependencies import require_role, get_db_session
from app.models.jwt import Role
from app.services.audit import log_audit
from app.models.audit import AuditAction, ResourceType

router = APIRouter()


@router.post("/", status_code=201)
async def provision_user(
    body: dict,
    request: Request,
    claims: dict = Depends(require_role(Role.admin)),
    conn=Depends(get_db_session),
):
    """
    Admin-only faculty/staff provisioning.

    This is the ONLY way a users row (and therefore a faculty_id) gets
    created. Login never auto-mints an account — see
    app/routes/auth.py::_lookup_provisioned_user — so anyone who hasn't
    been provisioned here first cannot authenticate, regardless of what
    Cognito says about them.
    """
    email = body.get("email")
    role_str = body.get("role", "faculty")
    department_code = body.get("department_code")
    name = body.get("name")

    if not email:
        raise HTTPException(status_code=400, detail="email is required")
    if role_str not in [r.value for r in Role]:
        raise HTTPException(status_code=400, detail="Invalid role")

    user_id = f"user_{uuid.uuid4().hex[:12]}"

    try:
        row = await conn.fetchrow(
            """
            INSERT INTO users (user_id, email, name, role, department_code)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING user_id, faculty_id, email, name, role, department_code
            """,
            user_id, email, name, role_str, department_code,
        )
    except Exception:
        raise HTTPException(status_code=409, detail="A user with this email already exists")

    await log_audit(
        action=AuditAction.USER_PROVISIONED,
        actor_type="user",
        actor_id=claims["sub"],
        resource_type=ResourceType.USER,
        resource_id=row["user_id"],
        details={"email": email, "role": role_str, "department_code": department_code},
        request=request,
    )

    return dict(row)


@router.get("/")
async def list_users(
    claims: dict = Depends(require_role(Role.hod, Role.admin)),
    conn=Depends(get_db_session),
):
    """List users in the current department (HOD) or all users (Admin)."""
    if claims["role"] == Role.admin.value:
        rows = await conn.fetch("SELECT user_id, email, role, department_code, is_active FROM users ORDER BY created_at DESC")
    else:
        rows = await conn.fetch(
            "SELECT user_id, email, role, department_code, is_active FROM users WHERE department_code = $1 ORDER BY created_at DESC",
            claims["department_code"],
        )
    return [dict(r) for r in rows]


@router.patch("/{user_id}/role")
async def update_user_role(
    user_id: str,
    body: dict,
    request: Request,
    claims: dict = Depends(require_role(Role.hod, Role.admin)),
    conn=Depends(get_db_session),
):
    """Change a user's role (HOD within dept, Admin globally)."""
    new_role = body.get("role")
    if new_role not in [r.value for r in Role]:
        raise HTTPException(status_code=400, detail="Invalid role")

    await conn.execute(
        "UPDATE users SET role = $1 WHERE user_id = $2",
        new_role, user_id
    )

    await log_audit(
        action=AuditAction.ROLE_CHANGED,
        actor_type="user",
        actor_id=claims["sub"],
        resource_type=ResourceType.USER,
        resource_id=user_id,
        details={"new_role": new_role},
        request=request,
    )

    return {"message": f"Role updated to {new_role}"}


@router.delete("/{user_id}")
async def delete_user(
    user_id: str,
    request: Request,
    claims: dict = Depends(require_role(Role.admin)),
    conn=Depends(get_db_session),
):
    """Soft-delete a user (Admin only)."""
    await conn.execute(
        "UPDATE users SET is_active = false WHERE user_id = $1",
        user_id
    )

    await log_audit(
        action=AuditAction.USER_DEACTIVATED,
        actor_type="user",
        actor_id=claims["sub"],
        resource_type=ResourceType.USER,
        resource_id=user_id,
        request=request,
    )

    return {"message": "User deactivated"}
