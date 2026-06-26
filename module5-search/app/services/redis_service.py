"""
Module 5 – Redis Service
Caching (search:* and facets:* key prefixes) + Pub/Sub invalidation listener.

Per the locked design:
  - Module 4 publishes to channel "search_invalidate" after writes
  - Module 5 subscribes and, on receipt, SCANs + DELs all search:* and
    facets:* keys (full invalidation, not surgical -- simpler and safe)
  - 300s TTL on search:* keys and 3600s TTL on facets:* keys remain as a
    safety net in case a pub/sub message is ever dropped
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any, Optional

import orjson
import redis.asyncio as aioredis

from app.config import settings

log = logging.getLogger(__name__)

SEARCH_KEY_PREFIX = "search:"
FACETS_KEY_PREFIX = "facets:"

_redis_client: Optional[aioredis.Redis] = None


def get_redis_client() -> aioredis.Redis:
    """Lazy-init shared Redis client (async)."""
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            max_connections=20,
        )
    return _redis_client


async def check_redis_health() -> bool:
    try:
        client = get_redis_client()
        await client.ping()
        return True
    except Exception as exc:
        log.error("Redis health check failed: %s", exc)
        return False


# ── Cache key generation ──────────────────────────────────────────────────

def make_search_cache_key(
    query: str,
    mode: str,
    department_code: str,
    filters: dict[str, Any],
    limit: int,
    cursor: Optional[str],
) -> str:
    """
    Deterministic cache key for a search request.
    Hashes the full parameter set so identical searches (even by different
    users in the same department) hit the same cache entry.
    """
    payload = orjson.dumps(
        {
            "query": query,
            "mode": mode,
            "dept": department_code,
            "filters": filters,
            "limit": limit,
            "cursor": cursor,
        },
        option=orjson.OPT_SORT_KEYS,
    )
    digest = hashlib.sha256(payload).hexdigest()[:32]
    return f"{SEARCH_KEY_PREFIX}{digest}"


def make_facets_cache_key(department_code: str) -> str:
    """Facet counts are cached per-department (RLS-scoped)."""
    return f"{FACETS_KEY_PREFIX}{department_code}"


# ── Cache read/write ───────────────────────────────────────────────────────

async def get_cached(key: str) -> Optional[dict[str, Any]]:
    """Fetch and deserialize a cached value, or None if missing/expired."""
    client = get_redis_client()
    raw = await client.get(key)
    if raw is None:
        return None
    try:
        return orjson.loads(raw)
    except orjson.JSONDecodeError:
        log.warning("Corrupt cache entry at key=%s, ignoring", key)
        return None


async def set_cached(key: str, value: dict[str, Any], ttl_seconds: int) -> None:
    """Serialize and store a value with a TTL."""
    client = get_redis_client()
    raw = orjson.dumps(value, default=_json_default)
    await client.set(key, raw, ex=ttl_seconds)


def _json_default(obj: Any) -> Any:
    """orjson fallback serializer for UUID/datetime not natively handled."""
    import uuid
    import datetime
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if isinstance(obj, (datetime.datetime, datetime.date)):
        return obj.isoformat()
    raise TypeError(f"Cannot serialize {type(obj)}")


# ── Invalidation ───────────────────────────────────────────────────────────

async def invalidate_all_search_caches() -> int:
    """
    SCAN + DEL all search:* and facets:* keys.
    Called when a pub/sub "paper_updated" message is received.
    Returns count of keys deleted.
    """
    client = get_redis_client()
    deleted = 0
    for prefix in (SEARCH_KEY_PREFIX, FACETS_KEY_PREFIX):
        cursor = 0
        while True:
            cursor, keys = await client.scan(cursor=cursor, match=f"{prefix}*", count=200)
            if keys:
                deleted += await client.delete(*keys)
            if cursor == 0:
                break
    log.info("Cache invalidation: deleted %d keys (search:* + facets:*)", deleted)
    return deleted


async def invalidate_facets_only() -> int:
    """Narrower invalidation -- just facets:* (used if you want to keep search
    result caches warm while still refreshing slow-changing facet counts)."""
    client = get_redis_client()
    deleted = 0
    cursor = 0
    while True:
        cursor, keys = await client.scan(cursor=cursor, match=f"{FACETS_KEY_PREFIX}*", count=200)
        if keys:
            deleted += await client.delete(*keys)
        if cursor == 0:
            break
    return deleted


# ── Pub/Sub listener (background task) ────────────────────────────────────

async def run_invalidation_listener() -> None:
    """
    Long-running background task: subscribes to the search_invalidate
    channel and triggers full cache invalidation on every message.

    Started from main.py's lifespan context. Designed to survive
    transient Redis disconnects by reconnecting with backoff.
    """
    backoff = 1
    while True:
        try:
            client = get_redis_client()
            pubsub = client.pubsub()
            await pubsub.subscribe(settings.REDIS_PUBSUB_CHANNEL)
            log.info(
                "Subscribed to Redis pub/sub channel: %s",
                settings.REDIS_PUBSUB_CHANNEL,
            )
            backoff = 1  # reset backoff after a successful (re)connection

            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                data = message["data"]
                log.info(
                    "Cache invalidation triggered by pub/sub message: %r", data
                )
                deleted = await invalidate_all_search_caches()
                log.info("Invalidated %d cache keys in response to: %r", deleted, data)

        except asyncio.CancelledError:
            log.info("Invalidation listener cancelled, shutting down cleanly.")
            raise
        except Exception as exc:
            log.error(
                "Invalidation listener error: %s. Reconnecting in %ds...",
                exc, backoff,
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)  # exponential backoff, capped at 30s


async def publish_invalidation(reason: str = "manual") -> None:
    """
    Manually trigger an invalidation message (useful for testing, or for
    Module 5 itself to publish if it ever needs to force a refresh).
    In production, Module 4 is the one calling PUBLISH after writes.
    """
    client = get_redis_client()
    await client.publish(settings.REDIS_PUBSUB_CHANNEL, reason)


async def close_redis() -> None:
    """Call on app shutdown."""
    global _redis_client
    if _redis_client is not None:
        await _redis_client.close()
        _redis_client = None
