"""
Redis idempotency guard for Module 3.
Prevents reprocessing the same idempotency_key.
TTL: 7 days (matches Module 2 dedup window).
"""
import logging
from typing import Optional

import redis as redis_lib

from app.config import settings

logger = logging.getLogger(__name__)

_redis_client: Optional[redis_lib.Redis] = None
_KEY_PREFIX = "m3:processed:"


def get_redis() -> redis_lib.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis_lib.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
        )
    return _redis_client


def is_already_processed(idempotency_key: str) -> bool:
    """Returns True if this key was already processed by Module 3."""
    try:
        r = get_redis()
        return bool(r.exists(f"{_KEY_PREFIX}{idempotency_key}"))
    except Exception as e:
        logger.warning(
            "Redis check failed for key '%s': %s — assuming not processed",
            idempotency_key, str(e),
        )
        return False


def mark_as_processed(idempotency_key: str) -> None:
    """Mark key as processed. Call AFTER successful Kafka produce."""
    try:
        r = get_redis()
        r.setex(
            f"{_KEY_PREFIX}{idempotency_key}",
            settings.redis_processed_ttl_seconds,
            "1",
        )
        logger.debug("Marked processed: %s", idempotency_key)
    except Exception as e:
        logger.error(
            "Redis mark_processed failed for key '%s': %s",
            idempotency_key, str(e),
        )
