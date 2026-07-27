"""Abstract contract for the odds feed.

Services depend on ``OddsProvider`` rather than a concrete vendor client (The
Odds API today), keeping the pricing source swappable and testable.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

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

    async def get_historical_odds(
        self,
        *,
        sport: str,
        at: datetime,
        markets: Sequence[str] = ("h2h",),
        regions: Sequence[str] = ("eu",),
    ) -> list[ProviderFixtureOdds]:
        """Return the bookmaker odds snapshot for ``sport`` as of ``at`` (UTC).

        Point-in-time historical odds: the feed returns the closest snapshot at
        or before ``at``. Same event/market shape as :meth:`get_odds`. Optional
        capability — providers without a historical feed leave this unimplemented.
        """
        raise NotImplementedError

    def pop_errors(self) -> dict[str, int]:
        """Return and clear per-source error counts (used by PrioritizedOddsProvider)."""
        return {}
