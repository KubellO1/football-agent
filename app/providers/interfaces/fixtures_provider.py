"""Abstract contract for the fixtures feed.

Services depend on ``FixturesProvider``, never on a concrete vendor client, so
the upstream data source (API-Football today) can be swapped or faked in tests
without touching business logic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from app.providers.schemas.fixtures import ProviderFixture


class FixturesProvider(ABC):
    """Read-only access to match fixtures from an external data feed."""

    @abstractmethod
    async def get_fixtures(
        self,
        *,
        on_date: date | None = None,
        league: str | int | None = None,
        season: int | None = None,
    ) -> list[ProviderFixture]:
        """Return fixtures, optionally filtered by date / league / season."""
        raise NotImplementedError

    @abstractmethod
    async def get_fixture(self, provider_id: str) -> ProviderFixture | None:
        """Return a single fixture by its provider id, or ``None`` if absent."""
        raise NotImplementedError
