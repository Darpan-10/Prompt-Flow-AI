from pydantic_settings import BaseSettings
from pydantic import ConfigDict, field_validator
from typing import List


class Settings(BaseSettings):
    model_config = ConfigDict(
        env_file=".env",
        env_file_encoding="utf-8"
    )

    app_env: str = "development"
    allowed_origins: List[str] = ["http://localhost:3000"]

    allowed_email_domains: List[str] = ["srmap.edu.in"]

    jwt_private_key_path: str = "keys/private.pem"
    jwt_public_key_path: str = "keys/public.pem"
    jwt_algorithm: str = "RS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7

    database_url: str = "postgresql://promptflow:secret@localhost:5432/promptflow"
    redis_url: str = "redis://localhost:6379"

    cognito_region: str = "ap-south-1"
    cognito_user_pool_id: str = ""
    cognito_client_id: str = ""
    cognito_client_secret: str = ""
    cognito_domain: str = ""

    jwt_issuer: str = "https://auth.promptflow.ai"

    rate_limit_attempts: int = 5
    rate_limit_window_seconds: int = 60

    @field_validator("allowed_email_domains", mode="before")
    @classmethod
    def parse_domains(cls, v):
        if isinstance(v, str):
            return [d.strip() for d in v.split(",") if d.strip()]
        return v


settings = Settings()