from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    # App
    app_env: str = "development"
    log_level: str = "INFO"

    # Domain constraint (HARD LOCK)
    allowed_domain: str = "srmap.edu.in"

    # Gmail OAuth2 — Service Account with Domain-Wide Delegation
    google_service_account_json: str = ""   # Full JSON from Secrets Manager
    gmail_delegated_user: str = ""          # e.g. papers@srmap.edu.in

    # Redis deduplication
    redis_url: str = "redis://localhost:6379"
    redis_dedup_ttl_seconds: int = 604800  # 7 days

    # AWS
    aws_region: str = "ap-south-1"
    s3_ingestion_bucket: str = "promptflow-ingestion-dev"
    s3_quarantine_bucket: str = "promptflow-quarantine-dev"
    s3_multipart_threshold_bytes: int = 5_242_880  # 5 MB

    # Kafka
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_topic_ingest: str = "ingest.raw"
    kafka_topic_dlq: str = "dlq.ingestion.failed"
    kafka_security_protocol: str = "PLAINTEXT"   # SASL_SSL for MSK
    kafka_sasl_mechanism: str = ""
    kafka_sasl_username: str = ""
    kafka_sasl_password: str = ""
    kafka_max_retries: int = 3

    # ClamAV
    clamav_host: str = "localhost"
    clamav_port: int = 3310
    clamav_timeout_seconds: int = 30

    # PostgreSQL (audit log)
    database_url: str = "postgresql://promptflow:secret@localhost:5432/promptflow"

    # Worker
    poll_interval_seconds: int = 60
    max_attachment_size_bytes: int = 52_428_800   # 50 MB hard limit

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
