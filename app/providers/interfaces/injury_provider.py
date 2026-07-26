"""Abstract contract for injury data feed.

Services depend on ``InjuryProvider``, never on a concrete vendor client.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.providers.schemas.injury import TeamInjuries


class InjuryProvider(ABC):
    """Read-only access to team injury / unavailability data."""

    @abstractmethod
    async def get_injuries(
        self,
        *,
        fixture_id: int,
    ) -> list[TeamInjuries]:
        """Return injury lists for both teams in a fixture.

        Returns up to 2 TeamInjuries (home + away). Empty list if no data.
        """
        raise NotImplementedError
