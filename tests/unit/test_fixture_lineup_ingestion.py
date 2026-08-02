"""比赛官方阵容同步服务的单元测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.exceptions import ExternalServiceError, NotFoundError, ValidationError
from app.models.entities.enums import PlayerPosition
from app.models.entities.fixture import Fixture
from app.models.entities.player import Player
from app.models.entities.team import Team
from app.models.value_objects.decision import EvidenceLevel
from app.models.value_objects.lineup import LineupStatus
from app.providers.interfaces.fixture_lineup_provider import FixtureLineupProvider
from app.providers.schemas.fixture_lineup import (
    ProviderFixtureLineupBatch,
    ProviderLineupPlayer,
    ProviderTeamLineup,
)
from app.services.fixture_lineup_ingestion import FixtureLineupIngestionService

SOURCE = "api-football"
FIXTURE_EXTERNAL_ID = "fixture-1"
CAPTURED_AT = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)


class _FakeProvider(FixtureLineupProvider):
    def __init__(self, batch: ProviderFixtureLineupBatch) -> None:
        self.batch = batch
        self.requested_ids: list[str] = []

    async def get_fixture_lineups(
        self,
        *,
        fixture_external_id: str,
    ) -> ProviderFixtureLineupBatch:
        self.requested_ids.append(fixture_external_id)
        return self.batch


def _team(name: str, external_id: str) -> Team:
    return Team(
        name=name,
        external_source=SOURCE,
        external_id=external_id,
    )


def _fixture(home: Team, away: Team) -> Fixture:
    return Fixture(
        competition_id=uuid4(),
        home_team_id=home.id,
        away_team_id=away.id,
        kickoff=datetime(2026, 8, 2, 18, 0, tzinfo=UTC),
        external_source=SOURCE,
        external_id=FIXTURE_EXTERNAL_ID,
    )


def _players(team: Team, prefix: str, count: int = 13) -> list[Player]:
    return [
        Player(
            name=f"{prefix} Player {index}",
            position=PlayerPosition.GOALKEEPER if index == 0 else PlayerPosition.DEFENDER,
            team_id=team.id,
            external_source=SOURCE,
            external_id=f"{prefix}-{index}",
        )
        for index in range(count)
    ]


def _provider_lineup(
    team: Team,
    players: list[Player],
    *,
    formation: str = "4-2-3-1",
) -> ProviderTeamLineup:
    records = [
        ProviderLineupPlayer(
            player_external_id=player.external_id,
            player_name=player.name,
            raw_position=player.position.value,
        )
        for player in players
        if player.external_id is not None
    ]
    return ProviderTeamLineup(
        team_external_id=team.external_id,
        formation=formation,
        starting=records[:11],
        substitutes=records[11:],
    )


def _batch(
    lineups: list[ProviderTeamLineup],
    *,
    source: str = SOURCE,
    fixture_external_id: str = FIXTURE_EXTERNAL_ID,
    response_complete: bool = True,
) -> ProviderFixtureLineupBatch:
    return ProviderFixtureLineupBatch(
        source=source,
        fixture_external_id=fixture_external_id,
        captured_at=CAPTURED_AT,
        response_complete=response_complete,
        lineups=lineups,
        request_reference=f"/fixtures/lineups?fixture={FIXTURE_EXTERNAL_ID}",
    )


def _service(
    batch: ProviderFixtureLineupBatch,
    *,
    fixture: Fixture | None,
    teams: list[Team],
    team_players: list[list[Player]],
    insert_results: list[bool] | None = None,
) -> tuple[FixtureLineupIngestionService, _FakeProvider, AsyncMock, AsyncMock]:
    provider = _FakeProvider(batch)
    fixtures = AsyncMock()
    fixtures.get_by_external_id.return_value = fixture
    team_repository = AsyncMock()
    team_repository.list_by_ids.return_value = teams
    player_repository = AsyncMock()
    player_repository.list_by_team.side_effect = team_players
    lineup_repository = AsyncMock()
    lineup_repository.add_if_absent.side_effect = insert_results or [True, True]
    return (
        FixtureLineupIngestionService(
            provider=provider,
            fixtures=fixtures,
            teams=team_repository,
            players=player_repository,
            lineups=lineup_repository,
            source=SOURCE,
            evidence_level=EvidenceLevel.B,
        ),
        provider,
        player_repository,
        lineup_repository,
    )


@pytest.mark.unit
async def test_sync_resolves_verified_identities_and_preserves_order() -> None:
    home = _team("Home", "home-1")
    away = _team("Away", "away-1")
    home_players = _players(home, "home")
    away_players = _players(away, "away")
    fixture = _fixture(home, away)
    batch = _batch(
        [
            _provider_lineup(home, home_players),
            _provider_lineup(away, away_players, formation="4-3-3"),
        ],
    )
    service, provider, player_repository, lineup_repository = _service(
        batch,
        fixture=fixture,
        teams=[away, home],
        team_players=[home_players, away_players],
    )

    report = await service.sync_fixture(fixture_external_id=" fixture-1 ")

    assert provider.requested_ids == [FIXTURE_EXTERNAL_ID]
    assert player_repository.list_by_team.await_count == 2
    assert report.lineups_received == 2
    assert report.players_received == 26
    assert report.lineups_created == 2
    assert report.lineups_unchanged == 0
    saved = [call.args[0] for call in lineup_repository.add_if_absent.await_args_list]
    assert saved[0].team_id == home.id
    assert saved[0].starting == tuple(player.id for player in home_players[:11])
    assert saved[0].substitutes == tuple(player.id for player in home_players[11:])
    assert saved[0].status is LineupStatus.CONFIRMED
    assert saved[0].source.evidence_level is EvidenceLevel.B
    assert str(saved[1].formation) == "4-3-3"


@pytest.mark.unit
async def test_sync_reports_idempotent_snapshots_as_unchanged() -> None:
    home = _team("Home", "home-1")
    away = _team("Away", "away-1")
    home_players = _players(home, "home")
    away_players = _players(away, "away")
    service, _, _, _ = _service(
        _batch(
            [
                _provider_lineup(home, home_players),
                _provider_lineup(away, away_players),
            ],
        ),
        fixture=_fixture(home, away),
        teams=[home, away],
        team_players=[home_players, away_players],
        insert_results=[False, False],
    )

    report = await service.sync_fixture(fixture_external_id=FIXTURE_EXTERNAL_ID)

    assert report.lineups_created == 0
    assert report.lineups_unchanged == 2


@pytest.mark.unit
async def test_empty_complete_batch_is_not_treated_as_provider_failure() -> None:
    home = _team("Home", "home-1")
    away = _team("Away", "away-1")
    service, _, player_repository, lineup_repository = _service(
        _batch([]),
        fixture=_fixture(home, away),
        teams=[home, away],
        team_players=[],
    )

    report = await service.sync_fixture(fixture_external_id=FIXTURE_EXTERNAL_ID)

    assert report.lineups_received == 0
    assert report.players_received == 0
    player_repository.list_by_team.assert_not_awaited()
    lineup_repository.add_if_absent.assert_not_awaited()


@pytest.mark.unit
async def test_missing_fixture_stops_before_provider_call() -> None:
    service, provider, _, lineup_repository = _service(
        _batch([]),
        fixture=None,
        teams=[],
        team_players=[],
    )

    with pytest.raises(NotFoundError, match="fixture not found"):
        await service.sync_fixture(fixture_external_id=FIXTURE_EXTERNAL_ID)

    assert provider.requested_ids == []
    lineup_repository.add_if_absent.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("source", "fixture_external_id", "response_complete", "error_type", "message"),
    [
        ("other", FIXTURE_EXTERNAL_ID, True, ValidationError, "source mismatch"),
        (SOURCE, "other", True, ValidationError, "does not match request"),
        (SOURCE, FIXTURE_EXTERNAL_ID, False, ExternalServiceError, "incomplete"),
    ],
)
async def test_invalid_batch_fails_before_player_resolution_and_writes(
    source: str,
    fixture_external_id: str,
    response_complete: bool,
    error_type: type[Exception],
    message: str,
) -> None:
    home = _team("Home", "home-1")
    away = _team("Away", "away-1")
    service, _, player_repository, lineup_repository = _service(
        _batch(
            [],
            source=source,
            fixture_external_id=fixture_external_id,
            response_complete=response_complete,
        ),
        fixture=_fixture(home, away),
        teams=[home, away],
        team_players=[],
    )

    with pytest.raises(error_type, match=message):
        await service.sync_fixture(fixture_external_id=FIXTURE_EXTERNAL_ID)

    player_repository.list_by_team.assert_not_awaited()
    lineup_repository.add_if_absent.assert_not_awaited()


@pytest.mark.unit
async def test_unverified_player_rejects_entire_batch_without_writes() -> None:
    home = _team("Home", "home-1")
    away = _team("Away", "away-1")
    home_players = _players(home, "home")
    away_players = _players(away, "away")
    service, _, _, lineup_repository = _service(
        _batch(
            [
                _provider_lineup(home, home_players),
                _provider_lineup(away, away_players),
            ],
        ),
        fixture=_fixture(home, away),
        teams=[home, away],
        team_players=[home_players[:-1], away_players],
    )

    with pytest.raises(ValidationError, match="unverified player identities"):
        await service.sync_fixture(fixture_external_id=FIXTURE_EXTERNAL_ID)

    lineup_repository.add_if_absent.assert_not_awaited()


@pytest.mark.unit
async def test_invalid_formation_rejects_entire_batch_without_writes() -> None:
    home = _team("Home", "home-1")
    away = _team("Away", "away-1")
    home_players = _players(home, "home")
    away_players = _players(away, "away")
    service, _, _, lineup_repository = _service(
        _batch(
            [
                _provider_lineup(home, home_players),
                _provider_lineup(away, away_players, formation="3-3-3"),
            ],
        ),
        fixture=_fixture(home, away),
        teams=[home, away],
        team_players=[home_players, away_players],
    )

    with pytest.raises(ValidationError, match="domain validation"):
        await service.sync_fixture(fixture_external_id=FIXTURE_EXTERNAL_ID)

    lineup_repository.add_if_absent.assert_not_awaited()
