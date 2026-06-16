"""
Central configuration — loaded once at startup.
All values can be overridden via environment variables or .env file.
"""
from __future__ import annotations

from functools import lru_cache
from typing import List, Optional

from pydantic import AnyUrl, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ───────────────────────────────────────────────────────
    app_name: str = "Indian Financial Platform"
    app_version: str = "1.0.0"
    debug: bool = False
    log_level: str = "INFO"
    secret_key: str = Field(default="change_me_in_production", min_length=16)

    # ── Database ──────────────────────────────────────────────────────────
    database_url: str = (
        "postgresql+asyncpg://finplatform:finplatform_secret@localhost:5432/indian_financials"
    )
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_timeout: int = 30

    # Sync URL for Alembic migrations
    @property
    def sync_database_url(self) -> str:
        return self.database_url.replace("+asyncpg", "+psycopg2")

    # ── External APIs ─────────────────────────────────────────────────────
    fmp_api_key: Optional[str] = None
    alpha_vantage_api_key: Optional[str] = None
    nse_user_agent: str = "Mozilla/5.0 (compatible; IndianFinPlatform/1.0)"

    # ── ETL Settings ──────────────────────────────────────────────────────
    max_companies_per_request: int = 100
    max_years: int = 10
    etl_concurrency: int = 4
    etl_timeout_seconds: int = 300
    cache_ttl_seconds: int = 86400      # 24 hours

    # Rate limits (requests per second per provider)
    screener_rate_limit_rps: float = 2.0
    nse_rate_limit_rps: float = 3.0
    bse_rate_limit_rps: float = 3.0
    fmp_rate_limit_rps: float = 5.0

    # Provider priority order (tried in sequence)
    provider_priority: List[str] = [
        "mca_xbrl",
        "screener",
        "nse",
        "bse",
        "fmp",
        "alpha_vantage",
    ]

    # ── Financial Thresholds ──────────────────────────────────────────────
    min_revenue_for_common_size: float = 0.01   # INR crores — avoid div/0
    mapping_confidence_threshold: float = 0.70  # below this → flagged for review
    quality_score_floor: float = 0.50           # below this → data excluded from peer agg

    # ── CORS ──────────────────────────────────────────────────────────────
    cors_origins: List[str] = ["http://localhost:8501", "http://localhost:3000"]

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if v.upper() not in allowed:
            raise ValueError(f"log_level must be one of {allowed}")
        return v.upper()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
