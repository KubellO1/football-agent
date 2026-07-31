"""球队阵容 Provider 契约与 DTO 测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from inspect import Parameter, iscoroutinefunction, signature

import pytest
from pydantic import ValidationError

from app.providers.interfaces.player_squad_provider import PlayerSquadProvider
from app.providers.schemas.player_squad import ProviderSquadBatch, ProviderSquadPlayer


@pytest.mark.unit
def test_provider_contract_is_abstract_and_async() -> None:
    assert "get_team_squad" in PlayerSquadProvider.__abstractmethods__
    assert iscoroutinefunction(PlayerSquadProvider.get_team_squad)


@pytest.mark.unit
def test_provider_query_requires_keyword_only_team_external_id() -> None:
    parameters = signature(PlayerSquadProvider.get_team_squad).parameters

    assert tuple(parameters) == ("self", "team_external_id")
    team_id = parameters["team_external_id"]
    assert team_id.kind is Parameter.KEYWORD_ONLY
    assert team_id.default is Parameter.empty


@pytest.mark.unit
def test_squad_player_normalizes_required_facts() -> None:
    record = ProviderSquadPlayer(
        player_external_id=" 99 ",
        player_name=" Test Player ",
        raw_position=" Midfielder ",
    )

    assert record.player_external_id == "99"
    assert record.player_name == "Test Player"
    assert record.raw_position == "Midfielder"


@pytest.mark.unit
@pytest.mark.parametrize(
    "field",
    ["player_external_id", "player_name", "raw_position"],
)
def test_squad_player_rejects_blank_required_facts(field: str) -> None:
    values = {
        "player_external_id": "99",
        "player_name": "Test Player",
        "raw_position": "Midfielder",
        field: " ",
    }

    with pytest.raises(ValidationError):
        ProviderSquadPlayer.model_validate(values)


@pytest.mark.unit
def test_batch_can_represent_complete_empty_squad() -> None:
    batch = ProviderSquadBatch(
        source=" api-football ",
        team_external_id=" 33 ",
        captured_at=datetime(2026, 7, 31, 10, 0, tzinfo=UTC),
        response_complete=True,
    )

    assert batch.source == "api-football"
    assert batch.team_external_id == "33"
    assert batch.records == []


@pytest.mark.unit
def test_batch_rejects_naive_capture_timestamp() -> None:
    with pytest.raises(ValidationError, match="captured_at must be timezone-aware"):
        ProviderSquadBatch(
            source="api-football",
            team_external_id="33",
            captured_at=datetime(2026, 7, 31, 10, 0),
            response_complete=True,
        )
