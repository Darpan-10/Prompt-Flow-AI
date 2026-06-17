"""
JWT Service — RS256 signing and verification.

Keys are loaded from PEM files (generated during setup).
For AWS, the private key is stored in Secrets Manager and
fetched at startup; the public key is also exposed via JWKS.
"""
import jwt
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
from pathlib import Path

from app.config import settings
from app.models.jwt import JWTPayload, Role, AuthType, get_permissions


def _load_key(path: str) -> str:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"Key not found at {path}. Run: python scripts/generate_keys.py"
        )
    return p.read_text()


def create_access_token(
    sub: str,
    email: str,
    name: str,
    role: Role,
    department_code: Optional[str] = None,
    auth_type: AuthType = AuthType.user,
    trace_id: Optional[str] = None,
) -> str:
    private_key = _load_key(settings.jwt_private_key_path)
    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=settings.access_token_expire_minutes)

    payload = {
        "sub": sub,
        "name": name,
        "email": email,
        "role": role.value,
        "department_code": department_code,
        "permissions": get_permissions(role),
        "iss": settings.jwt_issuer,
        "exp": int(exp.timestamp()),
        "iat": int(now.timestamp()),
        "auth_type": auth_type.value,
        "trace_id": trace_id or str(uuid.uuid4()),
    }

    return jwt.encode(payload, private_key, algorithm=settings.jwt_algorithm)


def create_m2m_token(client_id: str) -> str:
    private_key = _load_key(settings.jwt_private_key_path)
    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=15)

    payload = {
        "sub": client_id,
        "name": f"service:{client_id}",
        "email": f"{client_id}@internal",
        "role": Role.system_worker.value,
        "department_code": None,
        "permissions": get_permissions(Role.system_worker),
        "iss": settings.jwt_issuer,
        "exp": int(exp.timestamp()),
        "iat": int(now.timestamp()),
        "auth_type": AuthType.m2m.value,
        "trace_id": str(uuid.uuid4()),
    }

    return jwt.encode(payload, private_key, algorithm=settings.jwt_algorithm)


def verify_token(token: str) -> dict:
    """
    Verify and decode a JWT. Raises jwt.PyJWTError on failure.
    """
    public_key = _load_key(settings.jwt_public_key_path)
    return jwt.decode(
        token,
        public_key,
        algorithms=[settings.jwt_algorithm],
        options={"verify_exp": True},
    )
