from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from FINANCE_* environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="FINANCE_",
        extra="ignore",
    )

    app_name: str = "Finance Aggregator API"
    environment: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    database_url: str = Field(
        default="postgresql+psycopg://finance:finance@localhost:5432/finance",
        repr=False,
    )
    database_echo: bool = False
    database_pool_size: int = Field(default=5, ge=1, le=50)
    database_max_overflow: int = Field(default=10, ge=0, le=100)
    import_storage_root: Path = Path("/tmp/finance-aggregator/imports")
    import_max_file_size_bytes: int = Field(default=25 * 1024 * 1024, ge=1)
    import_xml_max_depth: int = Field(default=64, ge=1, le=256)
    import_xml_max_elements: int = Field(default=100_000, ge=1, le=1_000_000)
    import_xml_max_value_length: int = Field(default=65_536, ge=1, le=1_048_576)
    worker_poll_interval_seconds: float = Field(default=1.0, gt=0, le=60)
    fx_http_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    fx_max_attempts: int = Field(default=3, ge=1, le=5)
    fx_retry_backoff_seconds: float = Field(default=0.25, ge=0, le=10)
    fx_response_max_bytes: int = Field(default=512 * 1024, ge=1_024, le=2 * 1024 * 1024)
    fx_min_sync_interval_seconds: int = Field(default=60, ge=1, le=86_400)
    fx_auto_sync_enabled: bool = True
    fx_auto_sync_interval_seconds: int = Field(default=3600, ge=60, le=86_400)
    fx_stale_after_seconds: int = Field(default=72 * 60 * 60, ge=1, le=31 * 24 * 60 * 60)
    market_price_stale_after_seconds: int = Field(default=72 * 60 * 60, ge=1, le=31 * 24 * 60 * 60)
    crypto_aggregate_stale_after_seconds: int = Field(default=2 * 60 * 60, ge=60, le=24 * 60 * 60)
    market_http_timeout_seconds: float = Field(default=15.0, gt=0, le=30)
    market_response_max_bytes: int = Field(default=4 * 1024 * 1024, ge=1024, le=8 * 1024 * 1024)
    market_auto_sync_enabled: bool = True
    market_auto_sync_interval_seconds: int = Field(default=3600, ge=60, le=86_400)


@lru_cache
def get_settings() -> Settings:
    return Settings()
