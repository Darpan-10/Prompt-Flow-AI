"""
Shared mutable state for DB pool and Redis client.
Populated during FastAPI lifespan startup.
"""
import asyncpg
import redis.asyncio as redis
from typing import Optional

db_pool: Optional[asyncpg.Pool] = None
redis_client: Optional[redis.Redis] = None
