"""聚合预测仓储与逐 selection 决策记录隔离的集成测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from app.models.entities.prediction import MatchPrediction
from app.models.value_objects.metrics import ExpectedGoals
from app.models.value_objects.probability import Probability
from app.models.value_objects.score import MatchResult
from app.repositories.sqlalchemy.models import (
    PREDICTION_RECORD_DECISION,
    PredictionORM,
)
from app.repositories.sqlalchemy.prediction_repository import (
    SqlAlchemyPredictionRepository,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.entities.fixture import Fixture


@pytest.mark.integration
async def test_repository_ignores_newer_decision_record(
    db_session: AsyncSession,
    persisted_fixture: Fixture,
) -> None:
    repo = SqlAlchemyPredictionRepository(db_session)
    generated_at = datetime.now(UTC)
    aggregate = MatchPrediction(
        fixture_id=persisted_fixture.id,
        outcome_probabilities={
            MatchResult.HOME: Probability(0.5),
            MatchResult.DRAW: Probability(0.3),
            MatchResult.AWAY: Probability(0.2),
        },
        expected_goals=ExpectedGoals(home=1.6, away=0.9),
        model_version="ensemble-v1",
        generated_at=generated_at,
    )
    saved = await repo.add(aggregate)

    decision_id = uuid4()
    db_session.add(
        PredictionORM(
            id=decision_id,
            fixture_id=persisted_fixture.id,
            record_kind=PREDICTION_RECORD_DECISION,
            prediction_timestamp=generated_at + timedelta(minutes=1),
            final_decision="WATCH",
            generated_at=generated_at + timedelta(minutes=1),
        )
    )
    await db_session.flush()

    latest = await repo.get_by_fixture(persisted_fixture.id)

    assert latest is not None
    assert latest.id == saved.id
    assert latest.is_normalized
    assert await repo.get(decision_id) is None
