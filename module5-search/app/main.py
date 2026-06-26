"""
Module 5 – Main Application Entrypoint
FastAPI app with lifespan management: warms up the embedding model and
starts the Redis pub/sub invalidation listener as a background task.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

# pyrefly: ignore [missing-import]
from fastapi import FastAPI

from app.config import settings
from app.database import dispose_engine
from app.routes import health, search
from app.services import redis_service
from app.services.embedding_service import warm_up
# pyrefly: ignore [missing-import]
from sentence_transformers import SentenceTransformer

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger(__name__)

_invalidation_listener_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    log.info("Module 4 Storage API starting up...")
    # Load embedding model at startup to fail early if download fails
    log.info("Loading embedding model...")
    try:
        app.state.model = SentenceTransformer('all-mpnet-base-v2')
        log.info("Embedding model loaded successfully")
    except Exception as e:
        log.error(f"Failed to load embedding model: {e}")
        raise

    yield

    log.info("Module 4 Storage API shutting down...")

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
