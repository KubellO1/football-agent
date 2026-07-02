"""Provider response models (normalized DTOs returned by provider interfaces)."""

from __future__ import annotations

from app.providers.schemas.fixtures import ProviderFixture, ProviderTeam
from app.providers.schemas.odds import (
    BookmakerMarket,
    OddsOutcome,
    ProviderFixtureOdds,
)

__all__ = [
    "BookmakerMarket",
    "OddsOutcome",
    "ProviderFixture",
    "ProviderFixtureOdds",
    "ProviderTeam",
]
