from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite:///./local.db"
    pseudogram_api_key: str = ""
    pseudogram_base_url: str = "https://pseudogram-api.onrender.com"
    webhook_signature_required: bool = True
    dm_max_attempts: int = Field(default=5, ge=1, le=20)
    worker_poll_seconds: float = Field(default=0.5, gt=0, le=10)
    reconcile_initial_seconds: int = Field(default=5, ge=1, le=300)
    http_timeout_seconds: float = Field(default=15, gt=0, le=60)


@lru_cache
def get_settings() -> Settings:
    return Settings()

