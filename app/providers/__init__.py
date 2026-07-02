"""External-feed providers: contracts + implementations + composition helpers.

Mirrors ``app.agents``: interfaces express the contract, ``impl`` holds vendor
clients, and the ``build_*`` helpers are the single place that binds a concrete
vendor to its interface from settings. Callers depend on the interface only.
"""

from __future__ import annotations

from app.config.settings import Settings
from app.providers.impl.api_football_provider import ApiFootballProvider
from app.providers.impl.odds_api_provider import TheOddsApiProvider
from app.providers.interfaces.fixtures_provider import FixturesProvider
from app.providers.interfaces.odds_provider import OddsProvider


def build_fixtures_provider(settings: Settings) -> FixturesProvider:
    """Wire the fixtures provider (API-Football) from settings."""
    return ApiFootballProvider(
        api_key=settings.api_football_key,
        base_url=settings.api_football_base_url,
        timeout_seconds=settings.provider_timeout_seconds,
        max_retries=settings.provider_max_retries,
        backoff_base_seconds=settings.provider_backoff_base_seconds,
    )


def build_odds_provider(settings: Settings) -> OddsProvider:
    """Wire the odds provider (The Odds API) from settings."""
    return TheOddsApiProvider(
        api_key=settings.odds_api_key,
        base_url=settings.odds_api_base_url,
        timeout_seconds=settings.provider_timeout_seconds,
        max_retries=settings.provider_max_retries,
        backoff_base_seconds=settings.provider_backoff_base_seconds,
    )


__all__ = [
    "ApiFootballProvider",
    "FixturesProvider",
    "OddsProvider",
    "TheOddsApiProvider",
    "build_fixtures_provider",
    "build_odds_provider",
]
