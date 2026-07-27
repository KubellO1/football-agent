"""供应商 DTO 运行时类型解析的回归测试。"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

import pytest

from app.providers.schemas.fixtures import ProviderFixture
from app.providers.schemas.injury import PlayerInjury
from app.providers.schemas.odds import BookmakerMarket, ProviderFixtureOdds
from app.providers.schemas.sportmonks import SportmonksTransfer
from app.providers.schemas.weather import VenueWeather, WeatherCondition

if TYPE_CHECKING:
    from pydantic import BaseModel


@pytest.mark.unit
@pytest.mark.parametrize(
    "model",
    [
        ProviderFixture,
        PlayerInjury,
        BookmakerMarket,
        ProviderFixtureOdds,
        SportmonksTransfer,
        WeatherCondition,
        VenueWeather,
    ],
)
def test_datetime_models_generate_json_schema(model: type[BaseModel]) -> None:
    schema = model.model_json_schema()

    assert schema["type"] == "object"
    assert "properties" in schema


@pytest.mark.unit
def test_provider_fixture_parses_datetime_string() -> None:
    fixture = ProviderFixture.model_validate(
        {
            "provider_id": "fixture-1",
            "kickoff": "2026-07-27T18:30:00Z",
            "status": "NS",
            "home": {"provider_id": "home-1", "name": "Home"},
            "away": {"provider_id": "away-1", "name": "Away"},
        }
    )

    assert isinstance(fixture.kickoff, datetime)


@pytest.mark.unit
def test_player_injury_parses_date_string() -> None:
    injury = PlayerInjury.model_validate(
        {
            "player_id": 10,
            "team_id": 20,
            "fixture_date": "2026-07-27",
        }
    )

    assert injury.fixture_date == date(2026, 7, 27)
