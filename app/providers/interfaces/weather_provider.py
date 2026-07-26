"""Abstract contract for weather data feed.

Services depend on ``WeatherProvider``, never on a concrete vendor client.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from app.providers.schemas.weather import VenueWeather


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
    ) -> list[dict]:
        """Return sports events from WeatherAPI's sports endpoint.

        Raw upstream format — mapping to domain entities is a service-layer concern.
        """
        raise NotImplementedError
