"""比赛阵容仓储契约测试。"""

from __future__ import annotations

from inspect import Parameter, iscoroutinefunction, signature

import pytest

from app.models.entities.lineup import Lineup
from app.repositories.interfaces import LineupRepository
from app.repositories.interfaces.base import Repository


@pytest.mark.unit
def test_repository_contract_inherits_entity_repository() -> None:
    assert issubclass(LineupRepository, Repository)


@pytest.mark.unit
def test_repository_contract_is_abstract_and_async() -> None:
    expected_methods = {
        "get",
        "add",
        "add_if_absent",
        "list_by_fixture",
        "get_latest_by_source",
    }

    assert expected_methods <= LineupRepository.__abstractmethods__
    for method_name in expected_methods:
        assert iscoroutinefunction(getattr(LineupRepository, method_name))


@pytest.mark.unit
def test_fixture_query_exposes_explicit_as_of_boundary() -> None:
    parameters = signature(LineupRepository.list_by_fixture).parameters

    assert tuple(parameters) == (
        "self",
        "fixture_id",
        "team_id",
        "source",
        "status",
        "as_of",
    )
    assert parameters["fixture_id"].default is Parameter.empty
    for name in ("team_id", "source", "status", "as_of"):
        assert parameters[name].kind is Parameter.KEYWORD_ONLY
        assert parameters[name].default is None


@pytest.mark.unit
def test_latest_query_requires_team_and_source_with_optional_boundaries() -> None:
    parameters = signature(LineupRepository.get_latest_by_source).parameters

    assert tuple(parameters) == (
        "self",
        "fixture_id",
        "team_id",
        "source",
        "status",
        "as_of",
    )
    for name in ("fixture_id", "team_id", "source"):
        assert parameters[name].default is Parameter.empty
    for name in ("status", "as_of"):
        assert parameters[name].kind is Parameter.KEYWORD_ONLY
        assert parameters[name].default is None


@pytest.mark.unit
def test_repository_entity_type_matches_lineup() -> None:
    assert LineupRepository.__orig_bases__[0].__args__[0] is Lineup
