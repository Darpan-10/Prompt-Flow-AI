from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import asyncpg
import redis.asyncio as redis
import os
import logging

from app.routes import auth, m2m, users
from app.middleware.ratelimit import RateLimitMiddleware
from app import state

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting Prompt Flow Auth Service...")
    state.db_pool = await asyncpg.create_pool(
        os.getenv("DATABASE_URL"),
        min_size=5,
        max_size=20,
        command_timeout=30,
    )
    state.redis_client = redis.from_url(
        os.getenv("REDIS_URL", "redis://localhost:6379"),
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
    allow_origins=os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RateLimitMiddleware)

app.include_router(auth.router, prefix="/auth", tags=["Authentication"])
app.include_router(m2m.router, prefix="/auth/m2m", tags=["M2M"])
app.include_router(users.router, prefix="/users", tags=["Users"])


@app.get("/health", tags=["Health"])
async def health():
    return {"status": "ok", "service": "auth", "version": "1.0.0"}
