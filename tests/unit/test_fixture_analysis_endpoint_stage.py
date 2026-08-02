"""单场分析端点的阶段参数边界测试。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_fixture_analysis_service, get_fixture_repository
from app.api.v1.endpoints.analysis import router
from app.models.value_objects.analysis_stage import AnalysisStage
from app.services.fixture_analysis import FixtureAnalysisResult
from app.services.lineup_admission_gate import LineupAdmissionDecision

if TYPE_CHECKING:
    from uuid import UUID


class _FakeFixtureRepository:
    def __init__(self, fixture: object) -> None:
        self.fixture = fixture

    async def get(self, fixture_id: UUID) -> object:
        return self.fixture


@dataclass(frozen=True, slots=True)
class _FakeDetailedAnalysis:
    result: FixtureAnalysisResult
    lineup_admission: LineupAdmissionDecision


class _FakeAnalysisService:
    def __init__(self, fixture_id: UUID) -> None:
        self.fixture_id = fixture_id
        self.stages: list[AnalysisStage] = []

    async def analyze_detailed(
        self,
        fixture: object,
        *,
        stage: AnalysisStage = AnalysisStage.INITIAL,
    ) -> _FakeDetailedAnalysis:
        self.stages.append(stage)
        approved = not stage.requires_confirmed_lineups
        confidence_killer = None if approved else "lineup_admission_failed"
        return _FakeDetailedAnalysis(
            result=FixtureAnalysisResult(
                fixture_id=self.fixture_id,
                confidence_killer=confidence_killer,
            ),
            lineup_admission=LineupAdmissionDecision(
                approved=approved,
                reasons=("initial stage accepted" if approved else "verified lineups missing",),
            ),
        )


def _client() -> tuple[TestClient, _FakeAnalysisService, UUID]:
    fixture_id = uuid4()
    fixture = object()
    fixtures = _FakeFixtureRepository(fixture)
    analysis = _FakeAnalysisService(fixture_id)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_fixture_repository] = lambda: fixtures
    app.dependency_overrides[get_fixture_analysis_service] = lambda: analysis
    return TestClient(app), analysis, fixture_id


@pytest.mark.unit
@pytest.mark.parametrize(
    ("query", "expected_stage", "expected_approved"),
    [
        ("", AnalysisStage.INITIAL, True),
        ("?stage=initial", AnalysisStage.INITIAL, True),
        ("?stage=post_lineup", AnalysisStage.POST_LINEUP, False),
        ("?stage=final", AnalysisStage.FINAL, False),
    ],
)
def test_analysis_endpoint_forwards_explicit_stage(
    query: str,
    expected_stage: AnalysisStage,
    expected_approved: bool,
) -> None:
    client, analysis, fixture_id = _client()

    response = client.post(f"/fixtures/{fixture_id}/analyze{query}")

    assert response.status_code == 200
    assert analysis.stages == [expected_stage]
    payload = response.json()
    assert payload["analysis_stage"] == expected_stage.value
    assert payload["lineup_admission"]["approved"] is expected_approved
    assert payload["lineup_admission"]["reasons"]
    assert payload["confidence_killer"] == (
        None if expected_approved else "lineup_admission_failed"
    )


@pytest.mark.unit
def test_analysis_endpoint_rejects_unknown_stage_before_service_call() -> None:
    client, analysis, fixture_id = _client()

    response = client.post(f"/fixtures/{fixture_id}/analyze?stage=kickoff")

    assert response.status_code == 422
    assert analysis.stages == []
