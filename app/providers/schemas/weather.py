"""Response models for WeatherAPI feed.

Normalized DTOs returned by WeatherProvider after parsing upstream JSON.
WeatherAPI provides forecast and sports endpoints; this schema covers both.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - Pydantic 会在运行时解析字段注解
from typing import Any

from pydantic import BaseModel, Field


class WeatherCondition(BaseModel):
    """Weather condition at a specific time slice."""

    time: datetime
    temp_c: float
    feelslike_c: float
    humidity: int
    wind_kph: float
    wind_dir: str = ""
    precip_mm: float = 0.0
    condition_text: str = ""
    condition_code: int = 0


class VenueWeather(BaseModel):
    """Aggregated weather forecast for a match venue."""

    venue_name: str
    city: str = ""
    country: str = ""
    kickoff: datetime | None = None
    match_time_condition: WeatherCondition | None = None
    hourly_forecast: list[WeatherCondition] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)
