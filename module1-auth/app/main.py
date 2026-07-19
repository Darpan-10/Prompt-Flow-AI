from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import asyncpg
import redis.asyncio as redis
import os
import logging
from app.config import settings

from app.routes import auth, m2m, users, directory
from app.middleware.ratelimit import RateLimitMiddleware
from app import state

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting Prompt Flow Auth Service...")
    state.db_pool = await asyncpg.create_pool(
        settings.database_url,
        min_size=5,
        max_size=20,
        command_timeout=30,
    )

    state.redis_client = redis.from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=True,
    )
    logger.info("DB pool and Redis connected.")
    yield
    # Shutdown
    await state.db_pool.close()
    await state.redis_client.aclose()
    logger.info("Connections closed.")


app = FastAPI(
    title="Prompt Flow AI — Auth & Access Control",
    version="1.0.0",
    description="Module 1: Authentication, RBAC, JWT, and Audit Logging",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    # allow_credentials=True (needed: refresh_token is a cookie — see
    # app/routes/auth.py's set_cookie) means allow_origins can NOT be "*"
    # per the CORS spec — browsers silently reject that combination. So
    # instead of one hardcoded frontend port that doesn't exist in this
    # project, the default covers the common local-dev ports (React/Vite
    # defaults, plus this system's own services 8000-8003) so testing via
    # a browser — Swagger UI, a local dashboard, etc. — works out of the
    # box. Override with a comma-separated ALLOWED_ORIGINS for anything else.
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RateLimitMiddleware)

app.include_router(auth.router, prefix="/auth", tags=["Authentication"])
app.include_router(m2m.router, prefix="/auth/m2m", tags=["M2M"])
app.include_router(users.router, prefix="/users", tags=["Users"])
app.include_router(directory.router, prefix="/api/faculty", tags=["Directory"])


@app.get("/health", tags=["Health"])
async def health():
    return {"status": "ok", "service": "auth", "version": "1.0.0"}
