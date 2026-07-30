"""球员可用性观察仓储契约测试。"""

from __future__ import annotations

from inspect import Parameter, iscoroutinefunction, signature

import pytest

from app.models.entities.player_availability import PlayerAvailabilityObservation
from app.repositories.interfaces.base import Repository
from app.repositories.interfaces.player_availability_repository import (
    PlayerAvailabilityObservationRepository,
)


@pytest.mark.unit
def test_repository_contract_inherits_entity_repository() -> None:
    assert issubclass(PlayerAvailabilityObservationRepository, Repository)


@pytest.mark.unit
def test_repository_contract_is_abstract_and_async() -> None:
    expected_methods = {
        "get",
        "add",
        "add_if_absent",
        "list_by_fixture",
        "get_latest_by_source",
    }

    assert expected_methods <= PlayerAvailabilityObservationRepository.__abstractmethods__
    for method_name in expected_methods:
        method = getattr(PlayerAvailabilityObservationRepository, method_name)
        assert iscoroutinefunction(method)


@pytest.mark.unit
def test_fixture_query_exposes_explicit_as_of_boundary() -> None:
    parameters = signature(
        PlayerAvailabilityObservationRepository.list_by_fixture,
    ).parameters

    assert tuple(parameters) == (
        "self",
        "fixture_id",
        "team_id",
        "player_id",
        "source",
        "as_of",
    )
    for name in ("team_id", "player_id", "source", "as_of"):
        assert parameters[name].kind is Parameter.KEYWORD_ONLY
        assert parameters[name].default is None


@pytest.mark.unit
def test_latest_source_query_requires_source_and_keeps_as_of_optional() -> None:
    parameters = signature(
        PlayerAvailabilityObservationRepository.get_latest_by_source,
    ).parameters

    assert parameters["fixture_id"].default is Parameter.empty
    assert parameters["player_id"].default is Parameter.empty
    assert parameters["source"].default is Parameter.empty
    assert parameters["as_of"].kind is Parameter.KEYWORD_ONLY
    assert parameters["as_of"].default is None


@pytest.mark.unit
def test_repository_entity_type_matches_observation() -> None:
    assert (
        PlayerAvailabilityObservationRepository.__orig_bases__[0].__args__[0]
        is PlayerAvailabilityObservation
    )
