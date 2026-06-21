"""
Module 4 – Configuration
All settings loaded from environment variables / .env file.
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
    WORKER_ID: str = "module4-worker-1"

    # ── Database ─────────────────────────────────────────────────────
    DATABASE_URL: str = "postgresql+asyncpg://promptflow:secret@localhost:5433/promptflow"
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_TIMEOUT: int = 30
    DB_ECHO: bool = False

    # ── Kafka ─────────────────────────────────────────────────────────
    KAFKA_BOOTSTRAP_SERVERS: str = "localhost:9093"
    KAFKA_CONSUMER_GROUP: str = "module4-storage-worker"
    KAFKA_TOPIC_PAPERS_VALIDATED: str = "papers.validated"
    KAFKA_TOPIC_PAPERS_REVIEW: str = "papers.review"
    KAFKA_TOPIC_PAPERS_FAILED: str = "papers.failed"
    KAFKA_SECURITY_PROTOCOL: str = "PLAINTEXT"
    KAFKA_SASL_MECHANISM: str = ""
    KAFKA_SASL_USERNAME: str = ""
    KAFKA_SASL_PASSWORD: str = ""
    KAFKA_AUTO_OFFSET_RESET: str = "earliest"

    # ── Redis (idempotency) ───────────────────────────────────────────
    REDIS_URL: str = "redis://:localdevtoken@localhost:6380"
    REDIS_PROCESSED_TTL_SECONDS: int = 604800  # 7 days

    # ── FastAPI ───────────────────────────────────────────────────────
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8003
    API_PREFIX: str = "/api/v1"

    # ── AWS ───────────────────────────────────────────────────────────
    AWS_REGION: str = "ap-south-1"
    S3_INGESTION_BUCKET: str = "promptflow-ingestion-dev"

    # ── Search ────────────────────────────────────────────────────────
    VECTOR_DIMENSIONS: int = 768
    SEARCH_MAX_RESULTS: int = 50
    FULLTEXT_LANGUAGE: str = "english"

    # ── RLS context defaults ──────────────────────────────────────────
    DEFAULT_ACTOR_TYPE: str = "system"

    @property
    def kafka_topics(self) -> list[str]:
        return [
            self.KAFKA_TOPIC_PAPERS_VALIDATED,
            self.KAFKA_TOPIC_PAPERS_REVIEW,
            self.KAFKA_TOPIC_PAPERS_FAILED,
        ]

    @property
    def kafka_consumer_config(self) -> dict:
        cfg: dict = {
            "bootstrap.servers": self.KAFKA_BOOTSTRAP_SERVERS,
            "group.id": self.KAFKA_CONSUMER_GROUP,
            "auto.offset.reset": self.KAFKA_AUTO_OFFSET_RESET,
            "enable.auto.commit": False,
            "security.protocol": self.KAFKA_SECURITY_PROTOCOL,
        }
        if self.KAFKA_SASL_MECHANISM:
            cfg["sasl.mechanism"] = self.KAFKA_SASL_MECHANISM
            cfg["sasl.username"] = self.KAFKA_SASL_USERNAME
            cfg["sasl.password"] = self.KAFKA_SASL_PASSWORD
        return cfg


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
