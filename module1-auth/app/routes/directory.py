"""
Faculty Directory lookup — used by Module 3 to resolve a paper
submitter's email into a stable UUID + department/status.

Module 3 only has the local-part of a sender's email address at
extraction time (there's no session/JWT for the person who *sent* the
email — ingestion is fully automated). This endpoint is the only place
that lookup can be answered authoritatively, since Module 1 is the
sole owner of faculty_id (minted once at admin provisioning — see
app/routes/users.py POST /).

Restricted to system_worker (M2M) callers — this returns PII (name,
email) for arbitrary users by a caller-supplied key, so it can't be
left open to user-level tokens or the public internet.

Uses an admin-context connection rather than the normal get_db_session
dependency: get_db_session scopes RLS to the CALLER's own department,
but a system_worker token has no department_code of its own (it's not
tied to one faculty member) — under the standard users_dept_isolation
policy it would see zero rows for every department, not "current
department's rows". This lookup's entire purpose is cross-department
resolution for the automated pipeline, so it runs with the same
admin-context pattern as auth.py's login-time user lookup.
"""
from fastapi import APIRouter, Depends, HTTPException
from app.dependencies import require_role
from app.models.jwt import Role
from app import state

router = APIRouter()


@router.get("/{lookup_key}")
async def get_faculty(
    lookup_key: str,
    claims: dict = Depends(require_role(Role.system_worker)),
):
    """
    Resolve a faculty member by user_id, full email, or the local-part
    of their email (e.g. 'jdoe' matches 'jdoe@srmap.edu.in'). Returns
    404 if no match — Module 3's DirectoryServiceBase treats a 404 as
    faculty_status=not_found rather than an error.
    """
    async with state.db_pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.current_role', 'admin', true)")
            row = await conn.fetchrow(
                """
                SELECT user_id, faculty_id, name, email, department_code, is_active
                FROM users
                WHERE user_id = $1 OR email = $1 OR split_part(email, '@', 1) = $1
                LIMIT 1
                """,
                lookup_key,
            )

    if row is None:
        raise HTTPException(status_code=404, detail="faculty_not_found")

    return {
        "faculty_id": str(row["faculty_id"]),
        "faculty_name": row["name"],
        "faculty_email": row["email"],
        "department_code": row["department_code"],
        "faculty_status": "active" if row["is_active"] else "inactive",
    }
