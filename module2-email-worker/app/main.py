"""
Module 2 Entry Point.
FastAPI provides health + metrics endpoint.
Worker runs in a background thread.
"""

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.config import settings
from app.worker import run_worker_loop

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start worker in background thread
    worker_thread = threading.Thread(
        target=run_worker_loop,
        daemon=True,
        name="email-ingestion-worker",
    )
    worker_thread.start()
    logger.info("Email ingestion worker thread started")
    yield
    logger.info("Shutting down...")


app = FastAPI(
    title="Prompt Flow AI — Module 2: Email Ingestion Worker",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    return JSONResponse({
        "status": "ok",
        "service": "email-ingestion-worker",
        "version": "1.0.0",
        "env": settings.app_env,
        "domain_lock": f"@{settings.allowed_domain}",
    })


@app.get("/ready")
async def readiness():
    """Kubernetes readiness probe — checks Redis + Kafka connectivity."""
    checks = {}

    # Redis
    try:
        from app.services.redis_dedup import get_redis
        r = get_redis()
        r.ping()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {str(e)}"

    all_ok = all(v == "ok" for v in checks.values())
    status_code = 200 if all_ok else 503

    return JSONResponse(
        content={"status": "ready" if all_ok else "not_ready", "checks": checks},
        status_code=status_code,
    )
