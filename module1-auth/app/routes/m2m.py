"""
Machine-to-Machine (M2M) authentication.
Used by background workers in Modules 2, 3, etc.
"""
from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.services.cognito import verify_m2m_client
from app.services.jwt_service import create_m2m_token
from app.services.audit import log_audit
from app.models.audit import AuditAction

router = APIRouter()
security = HTTPBasic()


@router.post("/token")
async def m2m_token(
    request: Request,
    credentials: HTTPBasicCredentials = Depends(security),
):
    """
    Client Credentials grant for internal service accounts.
    Authenticate with Basic Auth (client_id:client_secret).
    Returns a 15-min JWT with system_worker role.
    """
    is_valid = await verify_m2m_client(
        credentials.username, credentials.password
    )

    if not is_valid:
        await log_audit(
            action=AuditAction.LOGIN_FAILED,
            actor_type="m2m",
            actor_id=credentials.username,
            details={"reason": "invalid_m2m_credentials"},
            request=request,
        )
        raise HTTPException(status_code=401, detail="Invalid service credentials")

    token = create_m2m_token(client_id=credentials.username)

    await log_audit(
        action=AuditAction.M2M_TOKEN_ISSUED,
        actor_type="system",
        actor_id=credentials.username,
        request=request,
    )

    return {
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": 900,
        "scope": "queue.consume db.write.internal",
    }
