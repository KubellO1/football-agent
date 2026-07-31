"""按比赛同步主客两队阵容的应用编排测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.exceptions import NotFoundError, ValidationError
from app.models.entities.fixture import Fixture
from app.models.entities.team import Team
from app.services.fixture_squad_ingestion import FixtureSquadIngestionService
from app.services.player_squad_ingestion import PlayerSquadIngestionReport

SOURCE = "api-football"
FIXTURE_EXTERNAL_ID = "fixture-1"


class _FakeSquadSynchronizer:
    def __init__(self) -> None:
        self.team_external_ids: list[str] = []

    async def sync_team(
        self,
        *,
        team_external_id: str,
    ) -> PlayerSquadIngestionReport:
        self.team_external_ids.append(team_external_id)
        return PlayerSquadIngestionReport(
            source=SOURCE,
            team_external_id=team_external_id,
            records_received=20,
            records_created=10,
            records_updated=2,
            records_unchanged=8,
        )


def _fixture(home_team: Team, away_team: Team) -> Fixture:
    return Fixture(
        competition_id=uuid4(),
        home_team_id=home_team.id,
        away_team_id=away_team.id,
        kickoff=datetime(2026, 8, 1, 18, 0, tzinfo=UTC),
        external_source=SOURCE,
        external_id=FIXTURE_EXTERNAL_ID,
    )


def _team(
    *,
    name: str,
    external_id: str | None,
    external_source: str | None = SOURCE,
) -> Team:
    return Team(
        name=name,
        external_source=external_source,
        external_id=external_id,
    )


def _service(
    *,
    fixture: Fixture | None,
    teams: list[Team],
) -> tuple[FixtureSquadIngestionService, _FakeSquadSynchronizer]:
    fixtures = AsyncMock()
    fixtures.get_by_external_id.return_value = fixture
    team_repository = AsyncMock()
    team_repository.list_by_ids.return_value = teams
    squads = _FakeSquadSynchronizer()
    return (
        FixtureSquadIngestionService(
            fixtures=fixtures,
            teams=team_repository,
            squads=squads,
            source=SOURCE,
        ),
        squads,
    )


@pytest.mark.unit
async def test_syncs_home_then_away_and_aggregates_reports() -> None:
    home = _team(name="Home", external_id="home-1")
    away = _team(name="Away", external_id="away-1")
    service, squads = _service(fixture=_fixture(home, away), teams=[away, home])

    report = await service.sync_fixture(fixture_external_id=" fixture-1 ")

    assert squads.team_external_ids == ["home-1", "away-1"]
    assert report.source == SOURCE
    assert report.fixture_external_id == FIXTURE_EXTERNAL_ID
    assert report.home_team.team_external_id == "home-1"
    assert report.away_team.team_external_id == "away-1"
    assert report.records_received == 40
    assert report.records_created == 20
    assert report.records_updated == 4
    assert report.records_unchanged == 16


@pytest.mark.unit
async def test_missing_fixture_stops_before_squad_calls() -> None:
    service, squads = _service(fixture=None, teams=[])

    with pytest.raises(NotFoundError, match="fixture not found"):
        await service.sync_fixture(fixture_external_id=FIXTURE_EXTERNAL_ID)

    assert squads.team_external_ids == []


@pytest.mark.unit
@pytest.mark.parametrize("missing_side", ["home", "away"])
async def test_missing_team_stops_before_squad_calls(missing_side: str) -> None:
    home = _team(name="Home", external_id="home-1")
    away = _team(name="Away", external_id="away-1")
    available = [away] if missing_side == "home" else [home]
    service, squads = _service(fixture=_fixture(home, away), teams=available)

    with pytest.raises(NotFoundError, match=f"fixture {missing_side} team not found"):
        await service.sync_fixture(fixture_external_id=FIXTURE_EXTERNAL_ID)

    assert squads.team_external_ids == []


@pytest.mark.unit
@pytest.mark.parametrize(
    ("invalid_side", "external_source", "external_id"),
    [
        ("home", SOURCE, None),
        ("home", "other-provider", "home-1"),
        ("away", SOURCE, None),
        ("away", "other-provider", "away-1"),
    ],
)
async def test_invalid_team_identity_stops_before_squad_calls(
    invalid_side: str,
    external_source: str,
    external_id: str | None,
) -> None:
    home = _team(name="Home", external_id="home-1")
    away = _team(name="Away", external_id="away-1")
    if invalid_side == "home":
        home = _team(
            name="Home",
            external_source=external_source,
            external_id=external_id,
        )
    else:
        away = _team(
            name="Away",
            external_source=external_source,
            external_id=external_id,
        )
    service, squads = _service(fixture=_fixture(home, away), teams=[home, away])

    with pytest.raises(ValidationError, match=f"fixture {invalid_side} team lacks"):
        await service.sync_fixture(fixture_external_id=FIXTURE_EXTERNAL_ID)

    assert squads.team_external_ids == []


@pytest.mark.unit
async def test_rejects_empty_fixture_external_id() -> None:
    service, squads = _service(fixture=None, teams=[])

    with pytest.raises(ValueError, match="fixture_external_id cannot be empty"):
        await service.sync_fixture(fixture_external_id="   ")

    assert squads.team_external_ids == []
