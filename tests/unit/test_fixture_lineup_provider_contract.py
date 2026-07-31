"""比赛官方阵容 Provider 契约与 DTO 测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from inspect import Parameter, iscoroutinefunction, signature

import pytest
from pydantic import ValidationError

from app.providers.interfaces.fixture_lineup_provider import FixtureLineupProvider
from app.providers.schemas.fixture_lineup import (
    ProviderFixtureLineupBatch,
    ProviderLineupPlayer,
    ProviderTeamLineup,
)

_CAPTURED_AT = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def _player(player_id: int) -> ProviderLineupPlayer:
    return ProviderLineupPlayer(
        player_external_id=str(player_id),
        player_name=f"Player {player_id}",
        raw_position="Midfielder",
        shirt_number=player_id % 100,
        grid_position="2:1",
    )


def _team_lineup(
    *,
    team_id: str,
    first_player_id: int,
) -> ProviderTeamLineup:
    return ProviderTeamLineup(
        team_external_id=team_id,
        formation="4-3-3",
        starting=[_player(value) for value in range(first_player_id, first_player_id + 11)],
        substitutes=[_player(value) for value in range(first_player_id + 11, first_player_id + 18)],
    )


@pytest.mark.unit
def test_provider_contract_is_abstract_and_async() -> None:
    assert "get_fixture_lineups" in FixtureLineupProvider.__abstractmethods__
    assert iscoroutinefunction(FixtureLineupProvider.get_fixture_lineups)


@pytest.mark.unit
def test_provider_query_requires_keyword_only_fixture_external_id() -> None:
    parameters = signature(FixtureLineupProvider.get_fixture_lineups).parameters

    assert tuple(parameters) == ("self", "fixture_external_id")
    fixture_id = parameters["fixture_external_id"]
    assert fixture_id.kind is Parameter.KEYWORD_ONLY
    assert fixture_id.default is Parameter.empty


@pytest.mark.unit
def test_lineup_player_normalizes_provider_facts() -> None:
    player = ProviderLineupPlayer(
        player_external_id=" 99 ",
        player_name=" Test Player ",
        raw_position=" Midfielder ",
        shirt_number=8,
        grid_position=" 2:1 ",
    )

    assert player.player_external_id == "99"
    assert player.player_name == "Test Player"
    assert player.raw_position == "Midfielder"
    assert player.grid_position == "2:1"


@pytest.mark.unit
def test_batch_accepts_complete_unpublished_lineups() -> None:
    batch = ProviderFixtureLineupBatch(
        source=" api-football ",
        fixture_external_id=" 1234 ",
        captured_at=_CAPTURED_AT,
        response_complete=True,
        request_reference=" /fixtures/lineups?fixture=1234 ",
    )

    assert batch.source == "api-football"
    assert batch.fixture_external_id == "1234"
    assert batch.lineups == []


@pytest.mark.unit
def test_batch_accepts_two_complete_team_lineups() -> None:
    batch = ProviderFixtureLineupBatch(
        source="api-football",
        fixture_external_id="1234",
        captured_at=_CAPTURED_AT,
        response_complete=True,
        lineups=[
            _team_lineup(team_id="10", first_player_id=100),
            _team_lineup(team_id="20", first_player_id=200),
        ],
    )

    assert len(batch.lineups) == 2
    assert len(batch.lineups[0].starting) == 11


@pytest.mark.unit
def test_team_lineup_requires_exactly_eleven_unique_starters() -> None:
    values = _team_lineup(team_id="10", first_player_id=100).model_dump()
    values["starting"] = values["starting"][:10]
    with pytest.raises(ValidationError, match="exactly eleven"):
        ProviderTeamLineup.model_validate(values)

    values = _team_lineup(team_id="10", first_player_id=100).model_dump()
    values["starting"][1] = values["starting"][0]
    with pytest.raises(ValidationError, match="starting players must be unique"):
        ProviderTeamLineup.model_validate(values)


@pytest.mark.unit
def test_team_lineup_rejects_overlapping_substitute() -> None:
    values = _team_lineup(team_id="10", first_player_id=100).model_dump()
    values["substitutes"][0] = values["starting"][0]

    with pytest.raises(ValidationError, match="both starting and a substitute"):
        ProviderTeamLineup.model_validate(values)


@pytest.mark.unit
def test_batch_rejects_partial_or_duplicate_teams() -> None:
    lineup = _team_lineup(team_id="10", first_player_id=100)
    common = {
        "source": "api-football",
        "fixture_external_id": "1234",
        "captured_at": _CAPTURED_AT,
        "response_complete": True,
    }

    with pytest.raises(ValidationError, match="zero or two teams"):
        ProviderFixtureLineupBatch(**common, lineups=[lineup])

    with pytest.raises(ValidationError, match="teams must be unique"):
        ProviderFixtureLineupBatch(**common, lineups=[lineup, lineup])


@pytest.mark.unit
def test_batch_rejects_player_in_both_teams() -> None:
    home = _team_lineup(team_id="10", first_player_id=100)
    away_values = _team_lineup(team_id="20", first_player_id=200).model_dump()
    away_values["starting"][0] = home.starting[0].model_dump()

    with pytest.raises(ValidationError, match="both team lineups"):
        ProviderFixtureLineupBatch(
            source="api-football",
            fixture_external_id="1234",
            captured_at=_CAPTURED_AT,
            response_complete=True,
            lineups=[home, ProviderTeamLineup.model_validate(away_values)],
        )


@pytest.mark.unit
def test_batch_rejects_naive_capture_timestamp() -> None:
    with pytest.raises(ValidationError, match="captured_at must be timezone-aware"):
        ProviderFixtureLineupBatch(
            source="api-football",
            fixture_external_id="1234",
            captured_at=datetime(2026, 8, 1, 12, 0),
            response_complete=True,
        )
