"""
Module 5 – Main Application Entrypoint
FastAPI app with lifespan management: warms up the embedding model and
starts the Redis pub/sub invalidation listener as a background task.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings
from app.database import dispose_engine
from app.routes import health, search
from app.services import redis_service
from app.services.embedding_service import warm_up

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger(__name__)

_invalidation_listener_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────────────────
    log.info("Module 5 Search service starting...")

    log.info("Warming up embedding model (sentence-transformers/all-mpnet-base-v2)...")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, warm_up)
    log.info("Embedding model ready.")

    global _invalidation_listener_task
    _invalidation_listener_task = asyncio.create_task(
        redis_service.run_invalidation_listener()
    )
    log.info(
        "Started Redis pub/sub invalidation listener (channel=%s)",
        settings.REDIS_PUBSUB_CHANNEL,
    )

    yield

    # ── Shutdown ─────────────────────────────────────────────────────────
    log.info("Module 5 Search service shutting down...")
    if _invalidation_listener_task:
        _invalidation_listener_task.cancel()
        try:
            await _invalidation_listener_task
        except asyncio.CancelledError:
            pass

    await redis_service.close_redis()
    await dispose_engine()
    log.info("Shutdown complete.")


app = FastAPI(
    title="PromptFlow AI — Module 5: Search & Discovery",
    description=(
        "Keyword (full-text), semantic (vector), and hybrid (RRF-fused) "
        "search over published papers. Read-only access to Module 4's "
        "PostgreSQL store; results scoped by RLS via JWT claims."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(search.router)
