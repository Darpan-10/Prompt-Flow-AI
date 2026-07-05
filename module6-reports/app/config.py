"""
Module 6 – Configuration
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

    # -- App ------------------------------------------------------------
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"

    # -- Database (READ-WRITE -- same shared Postgres as Module 4) ------
    # Module 6 reads `papers`/`validation_issues` (Module 4's tables) for
    # the compliance gate, and WRITES to its own `generated_reports` and
    # `report_checksums` tables, plus appends to the shared `audit_log`.
    DATABASE_URL: str = "postgresql+asyncpg://promptflow:secret@localhost:5433/promptflow"
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 10
    DB_POOL_TIMEOUT: int = 30
    DB_ECHO: bool = False

    # -- FastAPI ----------------------------------------------------------
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8006
    API_PREFIX: str = "/api/v1"

    # -- S3 (report storage) -----------------------------------------------
    AWS_REGION: str = "ap-south-1"
    S3_REPORTS_BUCKET: str = "promptflow-reports-dev"
    S3_PRESIGNED_URL_EXPIRY_SECONDS: int = 3600  # 1 hour, per locked spec
    # For local dev against a real AWS bucket (no local S3 emulator is
    # set up for this module -- see SETUP.md for why and the alternative).
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_ENDPOINT_URL: str = ""  # set for LocalStack/MinIO if you wire one up

    # -- JWT/Auth (from Module 1, same pattern as Module 5) ----------------
    JWT_ALGORITHM: str = "RS256"
    JWT_ISSUER: str = "https://auth.srmap.edu.in"
    JWT_AUDIENCE: str = "promptflow-api"
    SKIP_JWT_VALIDATION: bool = False
    JWT_PUBLIC_KEY: str = ""

    # -- Report generation --------------------------------------------------
    REPORT_TEMPLATE_DIR: str = "app/templates/reports"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
