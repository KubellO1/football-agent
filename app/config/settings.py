"""Application settings, loaded from environment / .env via pydantic-settings."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central, type-safe configuration.

    Values are read from environment variables (or a local ``.env`` file).
    Nothing here is football/betting specific — pure infrastructure config.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application ---
    app_name: str = "Football Agent"
    environment: str = "local"
    debug: bool = True
    api_v1_prefix: str = "/api/v1"
    log_level: str = "INFO"

    # --- PostgreSQL ---
    postgres_user: str = "football"
    postgres_password: str = "changeme"
    postgres_db: str = "football"
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    database_url: str | None = None  # explicit DSN overrides the parts above

    # --- Redis ---
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_url: str | None = None

    # --- AI reasoning (Claude) ---
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-4-8"

    # --- External data providers ---
    # API-Football (fixtures / results / league data).
    api_football_key: str = ""
    api_football_base_url: str = "https://v3.football.api-sports.io"
    # The Odds API (bookmaker odds across markets).
    odds_api_key: str = ""
    odds_api_base_url: str = "https://api.the-odds-api.com/v4"

    # Shared HTTP client behaviour for all providers.
    provider_timeout_seconds: float = 10.0
    provider_max_retries: int = 3  # extra attempts after the first, on transient errors
    provider_backoff_base_seconds: float = 0.5  # exponential backoff base

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sqlalchemy_dsn(self) -> str:
        """Async SQLAlchemy DSN (asyncpg driver)."""
        if self.database_url:
            return self.database_url
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def redis_dsn(self) -> str:
        if self.redis_url:
            return self.redis_url
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton (import-safe, testable via cache clear)."""
    return Settings()
