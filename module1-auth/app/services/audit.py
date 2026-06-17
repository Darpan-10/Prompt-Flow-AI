import json
import logging
from typing import Optional
from fastapi import Request

from app import state

logger = logging.getLogger(__name__)


async def log_audit(
    action: str,
    actor_type: str,
    actor_id: Optional[str],
    details: Optional[dict] = None,
    resource_id: Optional[str] = None,
    request: Optional[Request] = None,
):
    """
    Insert an immutable audit log entry.
    Falls back to stderr logging if DB is unavailable (never silently drops events).
    """
    ip = None
    trace_id = None

    if request:
        ip = request.client.host if request.client else None
        trace_id = request.headers.get("x-trace-id")

    try:
        async with state.db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO audit_log
                    (action, actor_type, actor_id, resource_id, details, ip_address, trace_id)
                VALUES ($1, $2, $3, $4, $5, $6::inet, $7::uuid)
                """,
                action,
                actor_type,
                actor_id,
                resource_id,
                json.dumps(details) if details else None,
                ip,
                trace_id,
            )
    except Exception as e:
        # Never swallow audit failures — at minimum log to stderr
        logger.error(
            "AUDIT_WRITE_FAILED action=%s actor=%s error=%s details=%s",
            action, actor_id, str(e), details,
        )
