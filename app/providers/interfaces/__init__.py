"""External-feed contracts (abstract interfaces)."""

from __future__ import annotations

from app.providers.interfaces.fixtures_provider import FixturesProvider
from app.providers.interfaces.odds_provider import OddsProvider

__all__ = ["FixturesProvider", "OddsProvider"]
