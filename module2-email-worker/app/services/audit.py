"""
Audit logging for Module 2, into Module 4's shared audit_log table.

config.py's database_url field already existed with a "(audit log)"
comment but nothing wrote to it — this wires that up. Uses psycopg2
(sync) rather than asyncpg since the worker loop is entirely
synchronous (see app/worker.py — Gmail API calls are sync, worker runs
in a plain background thread, not an event loop).

Same shape as Module 1's app/services/audit.py: resource_type is
required (Module 4's audit_log.resource_type is NOT NULL), failures
never raise — an audit write hiccup should not take down email
ingestion, so it logs to stderr instead.
"""
import json
import logging
import psycopg2

from app.config import settings

logger = logging.getLogger(__name__)


def log_audit(
    action: str,
    resource_type: str = "ingestion",
    resource_id: str = None,
    details: dict = None,
) -> None:
    try:
        conn = psycopg2.connect(settings.database_url, connect_timeout=3)
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO audit_log
                            (action, actor_type, actor_id, resource_type, resource_id, details)
                        VALUES (%s, 'system', %s, %s, %s, %s)
                        """,
                        (
                            action,
                            "module2-email-worker",
                            resource_type,
                            resource_id,
                            json.dumps(details) if details else None,
                        ),
                    )
        finally:
            conn.close()
    except Exception as e:
        logger.error(
            "AUDIT_WRITE_FAILED action=%s resource_type=%s resource_id=%s error=%s",
            action, resource_type, resource_id, str(e),
        )
