from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # App
    app_env: str = "development"
    log_level: str = "INFO"
    worker_id: str = "module3-worker-1"

    # Kafka — consumes from ingest.raw, produces to papers.*
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_consumer_group: str = "module3-ai-extraction"
    kafka_topic_ingest_raw: str = "ingest.raw"
    kafka_topic_papers_validated: str = "papers.validated"
    kafka_topic_papers_review: str = "papers.review"
    kafka_topic_papers_failed: str = "papers.failed"
    kafka_topic_dlq: str = "dlq.ingestion.failed"
    kafka_security_protocol: str = "PLAINTEXT"
    kafka_sasl_mechanism: str = ""
    kafka_sasl_username: str = ""
    kafka_sasl_password: str = ""

    # Redis — idempotency dedup
    redis_url: str = "redis://:localdevtoken@localhost:6379"
    redis_processed_ttl_seconds: int = 604800  # 7 days

    # AWS
    aws_region: str = "ap-south-1"
    s3_ingestion_bucket: str = "promptflow-ingestion-dev"

    # AWS Bedrock — LLM Fallback (Tier 4)
    bedrock_model_id: str = "anthropic.claude-3-haiku-20240307-v1:0"
    bedrock_max_tokens: int = 1500
    llm_confidence_threshold: float = 0.70   # ONLY invoke if below this
    llm_confidence_cap: float = 0.90         # Hard cap on Bedrock result

    # CrossRef API (Tier 2)
    crossref_api_url: str = "https://api.crossref.org/works"
    crossref_timeout_seconds: int = 10
    crossref_mailto: str = "promptflow@srmap.edu.in"

    # Directory / Faculty API
    directory_api_url: str = "http://localhost:8080"
    directory_timeout_seconds: int = 3
    directory_max_retries: int = 2

    # Routing thresholds
    default_confidence_threshold: float = 0.75

    # Database
    database_url: str = "postgresql://promptflow:secret@localhost:5432/promptflow"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
