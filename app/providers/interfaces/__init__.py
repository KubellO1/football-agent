"""External-feed contracts (abstract interfaces)."""

from __future__ import annotations

from app.providers.interfaces.fixtures_provider import FixturesProvider
from app.providers.interfaces.injury_provider import InjuryProvider
from app.providers.interfaces.odds_provider import OddsProvider

# from app.providers.interfaces.sportmonks_provider import SportmonksProvider  # DEPRECATED: 2026-07-17
from app.providers.interfaces.weather_provider import WeatherProvider

__all__ = [
    "FixturesProvider",
    "InjuryProvider",
    "OddsProvider",
    # "SportmonksProvider",  # DEPRECATED: 2026-07-17
    "WeatherProvider",
]
