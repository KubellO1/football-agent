"""Player 主数据身份与仓储契约测试。"""

from __future__ import annotations

from inspect import iscoroutinefunction, signature

import pytest

from app.models.entities.enums import PlayerPosition
from app.models.entities.player import Player
from app.repositories.interfaces.base import Repository
from app.repositories.interfaces.player_repository import PlayerRepository


@pytest.mark.unit
def test_player_normalizes_external_identity() -> None:
    player = Player(
        name="Test Player",
        position=PlayerPosition.FORWARD,
        external_source=" api-football ",
        external_id=" 12345 ",
    )

    assert player.external_source == "api-football"
    assert player.external_id == "12345"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("external_source", "external_id"),
    [
        ("api-football", None),
        (None, "12345"),
        (" ", "12345"),
        ("api-football", " "),
    ],
)
def test_player_rejects_incomplete_external_identity(
    external_source: str | None,
    external_id: str | None,
) -> None:
    with pytest.raises(ValueError):
        Player(
            name="Test Player",
            position=PlayerPosition.FORWARD,
            external_source=external_source,
            external_id=external_id,
        )


@pytest.mark.unit
def test_repository_contract_inherits_entity_repository() -> None:
    assert issubclass(PlayerRepository, Repository)


@pytest.mark.unit
def test_repository_contract_is_abstract_and_async() -> None:
    expected_methods = {
        "get",
        "add",
        "get_by_external_id",
        "list_by_team",
        "list_by_ids",
        "update",
    }

    assert expected_methods <= PlayerRepository.__abstractmethods__
    for method_name in expected_methods:
        assert iscoroutinefunction(getattr(PlayerRepository, method_name))


@pytest.mark.unit
def test_external_identity_query_requires_source_and_id() -> None:
    parameters = signature(PlayerRepository.get_by_external_id).parameters

    assert tuple(parameters) == ("self", "source", "external_id")
    assert parameters["source"].default is parameters["source"].empty
    assert parameters["external_id"].default is parameters["external_id"].empty


@pytest.mark.unit
def test_repository_entity_type_matches_player() -> None:
    assert PlayerRepository.__orig_bases__[0].__args__[0] is Player
