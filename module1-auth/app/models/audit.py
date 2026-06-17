# pyrefly: ignore [missing-import]
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, Literal
from datetime import datetime, UTC
import uuid


class AuditEvent(BaseModel):
    log_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    action: str
    actor_type: Literal["user", "system", "m2m"]
    actor_id: Optional[str]
    resource_id: Optional[str] = None
    details: Optional[Dict[str, Any]] = None
    ip_address: Optional[str] = None
    trace_id: Optional[str] = None
    logged_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# Audit action constants
class AuditAction:
    LOGIN_SUCCESS = "LOGIN_SUCCESS"
    LOGIN_FAILED = "LOGIN_FAILED"
    LOGOUT = "LOGOUT"
    TOKEN_REFRESHED = "TOKEN_REFRESHED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    DATA_ACCESSED = "DATA_ACCESSED"
    PASSWORD_CHANGED = "PASSWORD_CHANGED"
    MFA_ENABLED = "MFA_ENABLED"
    ACCOUNT_LOCKED = "ACCOUNT_LOCKED"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    M2M_TOKEN_ISSUED = "M2M_TOKEN_ISSUED"
