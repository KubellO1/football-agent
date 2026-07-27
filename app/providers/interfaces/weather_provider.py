"""Abstract contract for weather data feed.

Services depend on ``WeatherProvider``, never on a concrete vendor client.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from app.providers.schemas.weather import VenueWeather

type SportsEvent = dict[str, object]


class WeatherProvider(ABC):
    """Read-only access to venue weather / forecast data."""

    @abstractmethod
    async def get_venue_weather(
        self,
        *,
        city: str,
        dt: datetime | None = None,
    ) -> VenueWeather | None:
        """Return weather forecast for a city around ``dt`` (kickoff), or ``None`` if unavailable."""
        raise NotImplementedError

    @abstractmethod
    async def get_sports_events(
        self,
        *,
        sport: str = "football",
    ) -> list[SportsEvent]:
        """Return sports events from WeatherAPI's sports endpoint.

        Raw upstream format — mapping to domain entities is a service-layer concern.
        """
        raise NotImplementedError
