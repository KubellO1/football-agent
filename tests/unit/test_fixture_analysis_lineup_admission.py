"""FixtureAnalysisService 的阶段首发准入装配测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from app.models.entities.fixture import Fixture
from app.models.entities.lineup import Lineup
from app.models.value_objects.analysis_stage import AnalysisStage
from app.models.value_objects.decision import DataCompleteness, EvidenceLevel
from app.models.value_objects.lineup import Formation, LineupSource, LineupStatus
from app.models.value_objects.markets import MarketType, Selection
from app.models.value_objects.money import Money
from app.models.value_objects.odds import Odds
from app.models.value_objects.statistics import TeamStatistics
from app.services.fixture_analysis import FixtureAnalysisService, MatchAnalysisInputBuilder
from app.services.modeling import MarketQuote, ModelInput
from app.services.models.ensemble import EnsembleMatchModel
from app.services.models.lambda_estimator import LeagueAverages
from app.services.recommendation_gate import RecommendationGate
from app.services.verified_lineup import (
    VerifiedFixtureLineups,
    VerifiedLineupService,
    VerifiedTeamLineupResult,
)

_NOW = datetime(2026, 8, 2, 18, 0, tzinfo=UTC)


def _fixture() -> Fixture:
    return Fixture(
        competition_id=uuid4(),
        home_team_id=uuid4(),
        away_team_id=uuid4(),
        kickoff=_NOW + timedelta(hours=1),
    )


def _stats(*, goals_for: int, goals_against: int) -> TeamStatistics:
    return TeamStatistics(
        matches_played=10,
        wins=6,
        draws=2,
        losses=2,
        goals_for=goals_for,
        goals_against=goals_against,
        xg_for=float(goals_for),
        xg_against=float(goals_against),
    )


def _model_input(fixture: Fixture) -> ModelInput:
    return ModelInput(
        fixture=fixture,
        home_stats=_stats(goals_for=20, goals_against=10),
        away_stats=_stats(goals_for=10, goals_against=20),
        league=LeagueAverages(goals_per_game=1.4),
        quotes=[
            MarketQuote(
                selection=Selection(MarketType.MATCH_RESULT, "home"),
                odds=Odds(Decimal("2.50")),
            )
        ],
        bankroll=Money(Decimal("1000"), "EUR"),
        data_completeness=DataCompleteness(100.0),
        evidence_level=EvidenceLevel.A,
    )


def _lineup(fixture_id: UUID, team_id: UUID) -> Lineup:
    return Lineup(
        fixture_id=fixture_id,
        team_id=team_id,
        status=LineupStatus.CONFIRMED,
        source=LineupSource(name="api-football", evidence_level=EvidenceLevel.A),
        starting=tuple(uuid4() for _ in range(11)),
        substitutes=tuple(uuid4() for _ in range(7)),
        formation=Formation("4-3-3"),
        captured_at=_NOW,
    )


def _verified(fixture: Fixture) -> VerifiedFixtureLineups:
    return VerifiedFixtureLineups(
        fixture_id=fixture.id,
        as_of=_NOW,
        home=VerifiedTeamLineupResult(
            team_id=fixture.home_team_id,
            lineup=_lineup(fixture.id, fixture.home_team_id),
        ),
        away=VerifiedTeamLineupResult(
            team_id=fixture.away_team_id,
            lineup=_lineup(fixture.id, fixture.away_team_id),
        ),
    )


def _service(
    fixture: Fixture,
    *,
    verifier: AsyncMock | None,
) -> tuple[FixtureAnalysisService, AsyncMock]:
    builder = AsyncMock(spec=MatchAnalysisInputBuilder)
    builder.build.return_value = _model_input(fixture)
    return (
        FixtureAnalysisService(
            builder=cast("MatchAnalysisInputBuilder", builder),
            model=EnsembleMatchModel(),
            gate=RecommendationGate(),
            lineup_verifier=(
                cast("VerifiedLineupService", verifier) if verifier is not None else None
            ),
        ),
        builder,
    )


@pytest.mark.unit
async def test_initial_stage_does_not_query_lineups() -> None:
    fixture = _fixture()
    verifier = AsyncMock(spec=VerifiedLineupService)
    service, _ = _service(fixture, verifier=verifier)

    detailed = await service.analyze_detailed(
        fixture,
        as_of=_NOW,
        stage=AnalysisStage.INITIAL,
    )

    assert detailed.lineup_admission is not None
    assert detailed.lineup_admission.approved
    verifier.verify.assert_not_awaited()


@pytest.mark.unit
async def test_final_stage_fails_closed_without_lineup_verifier() -> None:
    fixture = _fixture()
    service, _ = _service(fixture, verifier=None)

    detailed = await service.analyze_detailed(
        fixture,
        as_of=_NOW,
        stage=AnalysisStage.FINAL,
    )

    assert detailed.model_output is not None
    assert detailed.model_output.candidates[0].stake is not None
    assert detailed.result.probabilities
    assert detailed.result.confidence_killer == "lineup_admission_failed"
    assert detailed.result.selections
    assert all(not selection.recommended for selection in detailed.result.selections)
    assert all(selection.kelly_fraction == 0.0 for selection in detailed.result.selections)
    assert all(selection.kelly_stake == 0.0 for selection in detailed.result.selections)
    assert all(
        "没有首发验证结果" in selection.reasons[-1] for selection in detailed.result.selections
    )


@pytest.mark.unit
async def test_final_stage_preserves_model_stake_when_verified_lineups_pass() -> None:
    fixture = _fixture()
    verifier = AsyncMock(spec=VerifiedLineupService)
    verifier.verify.return_value = _verified(fixture)
    service, builder = _service(fixture, verifier=verifier)

    detailed = await service.analyze_detailed(
        fixture,
        as_of=_NOW,
        stage=AnalysisStage.FINAL,
    )

    verifier.verify.assert_awaited_once_with(fixture, as_of=_NOW)
    builder.build.assert_awaited_once_with(fixture, as_of=_NOW)
    assert detailed.lineup_admission is not None
    assert detailed.lineup_admission.approved
    assert detailed.result.confidence_killer is None
    assert detailed.result.selections[0].kelly_fraction > 0.0
    assert detailed.result.selections[0].kelly_stake > 0.0


@pytest.mark.unit
async def test_lineup_failure_remains_auditable_when_model_input_is_missing() -> None:
    fixture = _fixture()
    service, builder = _service(fixture, verifier=None)
    builder.build.return_value = None

    detailed = await service.analyze_detailed(
        fixture,
        as_of=_NOW,
        stage=AnalysisStage.FINAL,
    )

    assert detailed.model_input is None
    assert detailed.result.confidence_killer == "lineup_admission_failed"
    assert detailed.lineup_admission is not None
    assert not detailed.lineup_admission.approved
