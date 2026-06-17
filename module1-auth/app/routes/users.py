"""
User management endpoints.
Only HOD and Admin can access these.
"""
from fastapi import APIRouter, Depends, HTTPException
from app.dependencies import require_role, get_db_session
from app.models.jwt import Role

router = APIRouter()


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
    return {"message": f"Role updated to {new_role}"}


@router.delete("/{user_id}")
async def delete_user(
    user_id: str,
    claims: dict = Depends(require_role(Role.admin)),
    conn=Depends(get_db_session),
):
    """Soft-delete a user (Admin only)."""
    await conn.execute(
        "UPDATE users SET is_active = false WHERE user_id = $1",
        user_id
    )
    return {"message": "User deactivated"}
