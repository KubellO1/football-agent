"""Application settings, loaded from environment / .env via pydantic-settings."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central, type-safe configuration.

    Values are read from environment variables (or a local ``.env`` file).
    Nothing here is football/betting specific — pure infrastructure config.
    """

    model_config = SettingsConfigDict(
        env_file="C:\\Users\\ruowa\\Projects\\football-agent\\.env",
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

    # --- AI reasoning (OpenAI) ---
    openai_api_key: str = ""
    openai_model: str = "gpt-5.6-sol"
    openai_reasoning_effort: Literal[
        "none",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    ] = "high"

    # --- External data providers ---
    # API-Football (fixtures / results / league data).
    api_football_key: str = ""
    api_football_base_url: str = "https://v3.football.api-sports.io"
    # The Odds API (bookmaker odds across markets).
    odds_api_key: str = ""
    odds_api_base_url: str = "https://api.the-odds-api.com/v4"
    # Odds-API.io (primary bookmaker odds provider).
    odds_api_io_api_key: str = ""
    odds_api_io_base_url: str = "https://api.odds-api.io/v3"
    # WeatherAPI (venue weather / sports events).
    weatherapi_key: str = ""
    # DEPRECATED: 2026-07-17 - Sportmonks removed from production. No consumer modules.
    # sportmonks_api_key: str = ""

    # Shared HTTP client behaviour for all providers.
    provider_timeout_seconds: float = 10.0
    provider_max_retries: int = 3  # extra attempts after the first, on transient errors
    provider_backoff_base_seconds: float = 0.5  # exponential backoff base

    # --- Odds ingestion ---
    # The Odds API sport keys to fetch (JSON list in env), and bookmaker regions.
    odds_sport_keys: list[str] = ["soccer_epl"]
    odds_regions: list[str] = ["eu"]
    # Max |kickoff - commence_time| tolerated when matching an odds event to a
    # fixture. Beyond this the event is treated as unmatched (never guessed).
    odds_match_tolerance_minutes: int = 180

    # --- Analysis ---
    # Bankroll used for Kelly stake sizing, and how many recent finished matches
    # form the team-form window.
    analysis_default_bankroll: float = 1000.0
    analysis_currency: str = "EUR"
    analysis_form_window: int = 10
    analysis_odds_max_age_minutes: int = Field(default=30, gt=0)
    analysis_odds_min_bookmakers: int = Field(default=2, ge=2)
    analysis_odds_max_relative_deviation: float = Field(default=0.2, gt=0.0, lt=1.0)
    # 概率校准温度（温度缩放）。1.0=不校准；>1 降低过度自信。由 fit_calibration 拟合得到。
    analysis_calibration_temperature: float = 1.0

    # --- Daily recommendations (cost control for the GPT review) ---
    # Thresholds a selection must clear (on top of the gate) to be worth a GPT
    # review, plus the daily cap on how many fixtures get reviewed.
    recommendations_min_ev: float = 0.05  # expected value >= 5%
    recommendations_min_kelly: float = 0.02  # Kelly fraction >= 2%
    recommendations_min_confidence: float = 0.70  # confidence >= 70%
    recommendations_max_picks: int = 5

    # --- Scheduled daily worker ---
    worker_schedule_time: str = "06:00"  # daily run time, HH:MM (UTC)
    worker_run_on_start: bool = False  # also run once immediately on startup

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
