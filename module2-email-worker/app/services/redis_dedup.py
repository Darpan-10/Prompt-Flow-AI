"""
Redis Deduplication Service.

Dedup keys checked (any match = skip):
  - Message-ID
  - In-Reply-To
  - References (each reference ID)
  - idempotency_key (sha256 of msg_id + filename)

TTL: 7 days (604800 seconds)
"""

import logging
import redis
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)

_redis_client: Optional[redis.Redis] = None


def get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
        )
    return _redis_client


def _dedup_key(value: str, prefix: str = "dedup") -> str:
    return f"{prefix}:{value}"


def is_duplicate(
    message_id: str,
    idempotency_key: str,
    in_reply_to: Optional[str] = None,
    references: Optional[str] = None,
) -> bool:
    """
    Check if this message is a duplicate.
    Returns True if any dedup key is already in Redis.
    """
    r = get_redis()
    keys_to_check = []

    if message_id:
        keys_to_check.append(_dedup_key(message_id, "msgid"))

    if idempotency_key:
        keys_to_check.append(_dedup_key(idempotency_key, "idem"))

    if in_reply_to:
        keys_to_check.append(_dedup_key(in_reply_to, "reply"))

    # Check each reference ID in the References header
    if references:
        for ref in references.split():
            ref = ref.strip("<>")
            if ref:
                keys_to_check.append(_dedup_key(ref, "ref"))

    if not keys_to_check:
        return False

    # Use pipeline for atomic multi-key check
    pipe = r.pipeline()
    for key in keys_to_check:
        pipe.exists(key)
    results = pipe.execute()

    for key, exists in zip(keys_to_check, results):
        if exists:
            logger.info("Duplicate detected via key: %s", key)
            return True

    return False


def mark_processed(
    message_id: str,
    idempotency_key: str,
    in_reply_to: Optional[str] = None,
) -> None:
    """
    Mark message as processed in Redis with TTL = 7 days.
    Called AFTER successful Kafka publish.
    """
    r = get_redis()
    pipe = r.pipeline()
    ttl = settings.redis_dedup_ttl_seconds

    pipe.setex(_dedup_key(message_id, "msgid"), ttl, "1")
    pipe.setex(_dedup_key(idempotency_key, "idem"), ttl, "1")

    if in_reply_to:
        pipe.setex(_dedup_key(in_reply_to, "reply"), ttl, "1")

    pipe.execute()
    logger.info(
        "Marked processed — message_id: %s | idempotency_key: %s (TTL: %ds)",
        message_id, idempotency_key, ttl,
    )
