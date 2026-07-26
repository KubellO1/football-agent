"""Provider response models (normalized DTOs returned by provider interfaces)."""

from __future__ import annotations

from app.providers.schemas.fixtures import ProviderFixture, ProviderTeam
from app.providers.schemas.injury import PlayerInjury, TeamInjuries
from app.providers.schemas.odds import (
    BookmakerMarket,
    OddsOutcome,
    ProviderFixtureOdds,
)
# from app.providers.schemas.sportmonks import (  # DEPRECATED: 2026-07-17
#     SportmonksFixturePredictions,
#     SportmonksOdds,
#     SportmonksPrediction,
#     SportmonksTeamStats,
#     SportmonksTransfer,
# )
from app.providers.schemas.weather import VenueWeather, WeatherCondition

__all__ = [
    "BookmakerMarket",
    "OddsOutcome",
    "PlayerInjury",
    "ProviderFixture",
    "ProviderFixtureOdds",
    "ProviderTeam",
    # "SportmonksFixturePredictions",  # DEPRECATED: 2026-07-17
    # "SportmonksOdds",  # DEPRECATED: 2026-07-17
    # "SportmonksPrediction",  # DEPRECATED: 2026-07-17
    # "SportmonksTeamStats",  # DEPRECATED: 2026-07-17
    # "SportmonksTransfer",  # DEPRECATED: 2026-07-17
    "TeamInjuries",
    "VenueWeather",
    "WeatherCondition",
]
