"""
Module 6 – Health Routes
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.database import check_db_health

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    service: str = "module6-reports"
    database: bool


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    db_ok = await check_db_health()
    return HealthResponse(status="ok" if db_ok else "degraded", database=db_ok)


@router.get("/ready")
async def ready() -> dict:
    return {"status": "ready"}
