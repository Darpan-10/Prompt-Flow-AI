"""
Module 5 – Health Routes
"""

from __future__ import annotations

from fastapi import APIRouter

from app.database import check_db_health
from app.schemas import HealthResponse
from app.services import redis_service
from app.services.embedding_service import is_model_loaded

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """
    Liveness/readiness probe. Checks DB, Redis, and whether the embedding
    model has been loaded (it's loaded lazily on first query OR eagerly
    via warm_up() at startup -- see app/main.py's lifespan).
    """
    db_ok = await check_db_health()
    redis_ok = await redis_service.check_redis_health()
    model_loaded = is_model_loaded()

    status = "ok" if (db_ok and redis_ok) else "degraded"

    return HealthResponse(
        status=status,
        database=db_ok,
        redis=redis_ok,
        embedding_model_loaded=model_loaded,
    )


@router.get("/ready")
async def ready() -> dict:
    """Simple readiness check for load balancer health checks."""
    return {"status": "ready"}
