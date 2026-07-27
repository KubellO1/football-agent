"""仓储接口泛型与继承关系的回归测试。"""

from __future__ import annotations

import pytest

from app.models.entities.base import Entity
from app.repositories.interfaces.base import Repository
from app.repositories.interfaces.decision_log_repository import DecisionLogRepository
from app.repositories.interfaces.fixture_repository import FixtureRepository
from app.repositories.interfaces.odds_snapshot_repository import OddsSnapshotRepository
from app.repositories.interfaces.prediction_repository import PredictionRepository
from app.repositories.interfaces.reference import (
    BookmakerRepository,
    CompetitionRepository,
    SeasonRepository,
    TeamRepository,
)
from app.repositories.interfaces.settlement_repository import (
    BankrollRepository,
    PerformanceSnapshotRepository,
    SettlementRepository,
)
from app.repositories.interfaces.value_bet_repository import ValueBetRepository


@pytest.mark.unit
def test_repository_type_parameter_is_bounded_to_entity() -> None:
    (entity_type,) = Repository.__type_params__

    assert entity_type.__bound__ is Entity


@pytest.mark.unit
@pytest.mark.parametrize(
    "repository_type",
    [
        FixtureRepository,
        DecisionLogRepository,
        OddsSnapshotRepository,
        PredictionRepository,
        TeamRepository,
        CompetitionRepository,
        BookmakerRepository,
        SeasonRepository,
        SettlementRepository,
        BankrollRepository,
        PerformanceSnapshotRepository,
        ValueBetRepository,
    ],
)
def test_concrete_repository_contract_inherits_base(
    repository_type: type[Repository[Entity]],
) -> None:
    assert issubclass(repository_type, Repository)
