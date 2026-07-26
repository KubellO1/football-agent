"""WeatherAPI implementation of :class:`WeatherProvider`.

Talks to WeatherAPI.com (https://www.weatherapi.com/). Auth is a ``key`` query
parameter. Supports forecast.json and sports.json endpoints.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from app.core.logging import get_logger
from app.providers.base import BaseHTTPProvider
from app.providers.interfaces.weather_provider import WeatherProvider
from app.providers.schemas.weather import VenueWeather, WeatherCondition

logger = get_logger(__name__)


class WeatherApiProvider(BaseHTTPProvider, WeatherProvider):
    """Weather feed backed by WeatherAPI.com."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.weatherapi.com/v1",
        timeout_seconds: float,
        max_retries: int,
        backoff_base_seconds: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            backoff_base_seconds=backoff_base_seconds,
            client=client,
        )
        self._api_key = api_key

    async def get_venue_weather(
        self,
        *,
        city: str,
        dt: datetime | None = None,
    ) -> VenueWeather | None:
        params: dict[str, Any] = {
            "key": self._api_key,
            "q": city,
            "days": 1,
        }
        if dt is not None:
            params["dt"] = dt.strftime("%Y-%m-%d")

        try:
            payload = await self._get_json("/forecast.json", params=params)
            return self._parse_forecast(payload, city, dt)
        except Exception:
            logger.warning("WeatherAPI forecast failed for city=%s", city, exc_info=True)
            return None

    async def get_sports_events(
        self,
        *,
        sport: str = "football",
    ) -> list[dict]:
        params: dict[str, Any] = {
            "key": self._api_key,
            "q": sport,
        }
        try:
            payload = await self._get_json("/sports.json", params=params)
            # sports.json wraps data in a "football" (or other sport) key
            if isinstance(payload, dict):
                return payload.get(sport, payload.get("football", []))
            return []
        except Exception:
            logger.warning("WeatherAPI sports endpoint failed for sport=%s", sport, exc_info=True)
            return []

    @staticmethod
    def _parse_forecast(
        payload: dict[str, Any], city: str, dt: datetime | None
    ) -> VenueWeather:
        location = payload.get("location", {})
        forecast = payload.get("forecast", {})
        forecastday = (forecast.get("forecastday") or [{}])[0]
        hours = forecastday.get("hour", [])

        hourly: list[WeatherCondition] = []
        match_condition: WeatherCondition | None = None

        target_hour = dt.hour if dt is not None else None

        for h in hours:
            wc = WeatherCondition(
                time=datetime.fromisoformat(h.get("time", "")),
                temp_c=float(h.get("temp_c", 0)),
                feelslike_c=float(h.get("feelslike_c", 0)),
                humidity=int(h.get("humidity", 0)),
                wind_kph=float(h.get("wind_kph", 0)),
                wind_dir=h.get("wind_dir", ""),
                precip_mm=float(h.get("precip_mm", 0)),
                condition_text=(h.get("condition") or {}).get("text", ""),
                condition_code=int((h.get("condition") or {}).get("code", 0)),
            )
            hourly.append(wc)
            if target_hour is not None and h.get("time", "").startswith(
                f"{dt:%Y-%m-%d} {target_hour:02d}"
            ):
                match_condition = wc

        return VenueWeather(
            venue_name="",
            city=location.get("name", city),
            country=location.get("country", ""),
            kickoff=dt,
            match_time_condition=match_condition,
            hourly_forecast=hourly,
            raw=payload,
        )
