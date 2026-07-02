"""Abstract contract for the odds feed.

Services depend on ``OddsProvider`` rather than a concrete vendor client (The
Odds API today), keeping the pricing source swappable and testable.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from app.providers.schemas.odds import ProviderFixtureOdds


class OddsProvider(ABC):
    """Read-only access to bookmaker odds from an external data feed."""

    @abstractmethod
    async def get_odds(
        self,
        *,
        sport: str,
        markets: Sequence[str] = ("h2h",),
        regions: Sequence[str] = ("eu",),
    ) -> list[ProviderFixtureOdds]:
        """Return current bookmaker odds for upcoming events in ``sport``.

        ``markets`` selects bet types (e.g. ``h2h``, ``totals``); ``regions``
        selects the bookmaker set (e.g. ``eu``, ``uk``).
        """
        raise NotImplementedError
