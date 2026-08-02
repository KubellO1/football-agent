"""FixtureAnalysisService 生产装配的首发准入接线测试。"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import Settings
from app.core.service_factory import build_fixture_analysis_service
from app.repositories.sqlalchemy.lineup_repository import SqlAlchemyLineupRepository
from app.services.lineup_admission_gate import LineupAdmissionGate
from app.services.modeling import MatchModel
from app.services.recommendation_gate import RecommendationGate
from app.services.verified_lineup import VerifiedLineupService

if TYPE_CHECKING:
    from app.core.container import Container


class _FakeContainer:
    def __init__(self) -> None:
        self.settings = Settings()
        self.model = MagicMock(spec=MatchModel)
        self.gate = RecommendationGate()

    def resolve(self, service_type: type[object]) -> object:
        if service_type is MatchModel:
            return self.model
        if service_type is RecommendationGate:
            return self.gate
        raise KeyError(service_type)


@pytest.mark.unit
def test_shared_factory_wires_verified_lineups_into_fixture_analysis() -> None:
    container = cast("Container", _FakeContainer())
    session = MagicMock(spec=AsyncSession)

    service = build_fixture_analysis_service(container, session)

    verifier = service._lineup_verifier
    assert isinstance(verifier, VerifiedLineupService)
    assert isinstance(verifier._repository, SqlAlchemyLineupRepository)
    assert isinstance(service._lineup_gate, LineupAdmissionGate)
