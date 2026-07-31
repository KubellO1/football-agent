"""External-feed providers: contracts + implementations + composition helpers.

Mirrors ``app.agents``: interfaces express the contract, ``impl`` holds vendor
clients, and the ``build_*`` helpers are the single place that binds a concrete
vendor to its interface from settings. Callers depend on the interface only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.providers.impl.api_football_player_availability_provider import (
    ApiFootballPlayerAvailabilityProvider,
)
from app.providers.impl.api_football_provider import ApiFootballProvider
from app.providers.impl.injury_provider import ApiFootballInjuryProvider
from app.providers.impl.odds_api_io_provider import OddsApiIoProvider
from app.providers.impl.odds_api_provider import TheOddsApiProvider
from app.providers.impl.prioritized_odds_provider import PrioritizedOddsProvider

# from app.providers.impl.sportmonks_provider import SportmonksApiProvider  # DEPRECATED: 2026-07-17
from app.providers.impl.weather_provider import WeatherApiProvider
from app.providers.interfaces.fixtures_provider import FixturesProvider
from app.providers.interfaces.injury_provider import InjuryProvider
from app.providers.interfaces.odds_provider import OddsProvider
from app.providers.interfaces.player_availability_provider import (
    PlayerAvailabilityProvider,
)

# from app.providers.interfaces.sportmonks_provider import SportmonksProvider  # DEPRECATED: 2026-07-17
from app.providers.interfaces.weather_provider import WeatherProvider
from app.utils.rate_limiter import TokenBucketRateLimiter

if TYPE_CHECKING:
    from app.config.settings import Settings
    from app.database.redis import RedisConnection

# Module-level rate limiter (shared across worker restarts via optional Redis)
rate_limiter: TokenBucketRateLimiter | None = None


def build_fixtures_provider(settings: Settings) -> FixturesProvider:
    """Wire the fixtures provider (API-Football) from settings."""
    return ApiFootballProvider(
        api_key=settings.api_football_key,
        base_url=settings.api_football_base_url,
        timeout_seconds=settings.provider_timeout_seconds,
        max_retries=settings.provider_max_retries,
        backoff_base_seconds=settings.provider_backoff_base_seconds,
    )


def build_odds_provider(
    settings: Settings,
    redis: RedisConnection | None = None,
) -> OddsProvider:
    """Wire the odds providers: Odds-API.io (primary) → The Odds API (fallback)."""
    global rate_limiter
    if rate_limiter is None:
        rate_limiter = TokenBucketRateLimiter(budget=100, redis=redis)
    primary = OddsApiIoProvider(
        api_key=settings.odds_api_io_api_key,
        base_url=settings.odds_api_io_base_url,
        timeout_seconds=settings.provider_timeout_seconds,
        max_retries=settings.provider_max_retries,
        backoff_base_seconds=settings.provider_backoff_base_seconds,
        rate_limiter=rate_limiter,
        redis=redis,
        cache_ttl=300,
    )
    fallback = TheOddsApiProvider(
        api_key=settings.odds_api_key,
        base_url=settings.odds_api_base_url,
        timeout_seconds=settings.provider_timeout_seconds,
        max_retries=settings.provider_max_retries,
        backoff_base_seconds=settings.provider_backoff_base_seconds,
    )
    return PrioritizedOddsProvider(primary=primary, fallback=fallback)


def build_weather_provider(settings: Settings) -> WeatherProvider:
    """Wire the weather provider (WeatherAPI) from settings."""
    return WeatherApiProvider(
        api_key=settings.weatherapi_key,
        timeout_seconds=settings.provider_timeout_seconds,
        max_retries=settings.provider_max_retries,
        backoff_base_seconds=settings.provider_backoff_base_seconds,
    )


# def build_sportmonks_provider(settings: Settings) -> SportmonksProvider:  # DEPRECATED: 2026-07-17
#     """Wire the Sportmonks provider from settings."""
#     return SportmonksApiProvider(
#         api_key=settings.sportmonks_api_key,
#         timeout_seconds=settings.provider_timeout_seconds,
#         max_retries=settings.provider_max_retries,
#         backoff_base_seconds=settings.provider_backoff_base_seconds,
#     )


def build_injury_provider(settings: Settings) -> InjuryProvider:
    """Wire the injury provider (API-Football injuries endpoint) from settings.

    Reuses the same API-Football key as the fixtures provider.
    """
    return ApiFootballInjuryProvider(
        api_key=settings.api_football_key,
        base_url=settings.api_football_base_url,
        timeout_seconds=settings.provider_timeout_seconds,
        max_retries=settings.provider_max_retries,
        backoff_base_seconds=settings.provider_backoff_base_seconds,
    )


def build_player_availability_provider(
    settings: Settings,
) -> PlayerAvailabilityProvider:
    """使用 API-Football 构造严格的球员可用性 Provider。"""
    return ApiFootballPlayerAvailabilityProvider(
        api_key=settings.api_football_key,
        base_url=settings.api_football_base_url,
        timeout_seconds=settings.provider_timeout_seconds,
        max_retries=settings.provider_max_retries,
        backoff_base_seconds=settings.provider_backoff_base_seconds,
    )


__all__ = [
    "ApiFootballInjuryProvider",
    "ApiFootballPlayerAvailabilityProvider",
    "ApiFootballProvider",
    "FixturesProvider",
    "InjuryProvider",
    "OddsApiIoProvider",
    "OddsProvider",
    "PlayerAvailabilityProvider",
    "PrioritizedOddsProvider",
    # "SportmonksApiProvider",  # DEPRECATED: 2026-07-17
    # "SportmonksProvider",  # DEPRECATED: 2026-07-17
    "TheOddsApiProvider",
    "WeatherApiProvider",
    "WeatherProvider",
    "build_fixtures_provider",
    "build_injury_provider",
    "build_odds_provider",
    "build_player_availability_provider",
    # "build_sportmonks_provider",  # DEPRECATED: 2026-07-17
    "build_weather_provider",
]
