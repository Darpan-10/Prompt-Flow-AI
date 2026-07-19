from pydantic import BaseModel, EmailStr
from typing import List, Optional, Literal
from enum import Enum


class Role(str, Enum):
    faculty = "faculty"
    coordinator = "coordinator"
    hod = "hod"
    admin = "admin"
    system_worker = "system_worker"


class AuthType(str, Enum):
    user = "user"
    m2m = "m2m"


# Permissions per role
ROLE_PERMISSIONS: dict[Role, List[str]] = {
    Role.faculty: [
        "paper.view.self",
        "paper.submit",
        "paper.reprocess.self",
        "search.dept",
    ],
    Role.coordinator: [
        "paper.view.self",
        "paper.submit",
        "paper.reprocess.self",
        "paper.review",
        "paper.approve",
        "export.create",
        "search.dept",
    ],
    Role.hod: [
        "paper.view.self",
        "paper.submit",
        "paper.review",
        "paper.approve",
        "export.create",
        "user.manage",
        "audit.view",
        "metrics.view",
        "search.dept",
    ],
    Role.admin: [
        "paper.view.all",
        "paper.submit",
        "paper.review",
        "paper.approve",
        "export.create",
        "export.global",
        "user.manage",
        "user.delete",
        "audit.view",
        "metrics.view",
        "system.config",
        "search.global",
    ],
    Role.system_worker: [
        "queue.consume",
        "db.write.internal",
    ],
}


def get_permissions(role: Role) -> List[str]:
    return ROLE_PERMISSIONS.get(role, [])


class JWTPayload(BaseModel):
    sub: str
    name: str
    email: str
    role: Role
    department_code: Optional[str] = None
    permissions: List[str]
    iss: str
    aud: str = "promptflow-api"
    # UUID minted once at admin provisioning (users.faculty_id). Optional
    # because m2m/system_worker tokens aren't tied to a faculty record.
    faculty_id: Optional[str] = None
    exp: int
    iat: int
    auth_type: AuthType
    trace_id: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int = 900


class UserInfo(BaseModel):
    sub: str
    email: str
    name: str
    role: Role
    department_code: Optional[str]
    permissions: List[str]
    faculty_id: Optional[str] = None
