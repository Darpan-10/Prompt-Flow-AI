"""
Module 5 – Configuration
Settings loaded from environment variables / .env file.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ──────────────────────────────────────────────────────────
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"
    WORKER_ID: str = "module5-search-1"

    # ── Database (Read-Only to Module 4) ─────────────────────────────
    # Same RDS instance as Module 4, but Module 5 only reads (SELECT)
    DATABASE_URL: str = "postgresql+asyncpg://promptflow:secret@localhost:5433/promptflow"
    DB_POOL_SIZE: int = 5  # Smaller than Module 4 (read-only, less contention)
    DB_MAX_OVERFLOW: int = 10
    DB_POOL_TIMEOUT: int = 30
    DB_ECHO: bool = False

    # ── FastAPI ───────────────────────────────────────────────────────
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8005
    API_PREFIX: str = "/api/v1"

    # ── Redis (Caching + Pub/Sub Invalidation) ────────────────────────
    # Shared with Module 4; use different key prefixes (search:*, facets:*)
    REDIS_URL: str = "redis://:localdevtoken@localhost:6380"
    REDIS_SEARCH_CACHE_TTL: int = 300  # 5 minutes for search results
    REDIS_FACETS_CACHE_TTL: int = 3600  # 1 hour for facet counts
    REDIS_PUBSUB_CHANNEL: str = "search_invalidate"

    # ── Search Configuration ───────────────────────────────────────────
    VECTOR_DIMENSIONS: int = 768
    EMBEDDING_MODEL: str = "all-mpnet-base-v2"
    SEARCH_RESULTS_MAX: int = 100
    SEARCH_RESULTS_DEFAULT: int = 20
    QUERY_MAX_LENGTH: int = 200
    RRF_K: int = 60  # Reciprocal Rank Fusion constant

    # ── JWT/Auth (from Module 1) ──────────────────────────────────────
    JWT_ALGORITHM: str = "RS256"
    JWT_ISSUER: str = "https://auth.srmap.edu.in"
    JWT_AUDIENCE: str = "promptflow-api"
    # In local testing, set SKIP_JWT_VALIDATION=true to bypass verification
    SKIP_JWT_VALIDATION: bool = False
    JWT_PUBLIC_KEY: str = ""  # RS256 public key (PEM format) from Module 1, required if SKIP_JWT_VALIDATION=false


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
