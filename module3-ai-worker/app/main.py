"""
Module 3 Entry Point.
FastAPI serves /health and /ready.
Worker runs in background thread.
"""
import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.config import settings
from app.pipeline import run_worker_loop

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    worker_thread = threading.Thread(
        target=run_worker_loop,
        daemon=True,
        name="module3-ai-extraction-worker",
    )
    worker_thread.start()
    logger.info("Module 3 AI extraction worker thread started")
    yield
    logger.info("Module 3 shutting down")


app = FastAPI(
    title="Prompt Flow AI — Module 3: AI Extraction Worker",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    return JSONResponse({
        "status": "ok",
        "service": "ai-extraction-worker",
        "version": "1.0.0",
        "env": settings.app_env,
    })


@app.get("/ready")
async def readiness():
    checks = {}

    try:
        from app.services.idempotency import get_redis
        get_redis().ping()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {str(e)}"

    try:
        from app.services.directory.service import get_directory_service
        import httpx
        async with httpx.AsyncClient(timeout=2) as client:
            r = await client.get(
                f"{settings.directory_api_url}/health"
            )
        checks["directory_api"] = "ok" if r.status_code == 200 else f"http_{r.status_code}"
    except Exception as e:
        checks["directory_api"] = f"error: {str(e)}"

    all_ok = all(v == "ok" for v in checks.values())
    return JSONResponse(
        content={"status": "ready" if all_ok else "degraded", "checks": checks},
        status_code=200 if all_ok else 503,
    )
