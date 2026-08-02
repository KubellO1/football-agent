"""阶段感知首发准入 Gate 的纯逻辑单元测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest

from app.models.entities.lineup import Lineup
from app.models.value_objects.analysis_stage import AnalysisStage
from app.models.value_objects.decision import EvidenceLevel
from app.models.value_objects.lineup import Formation, LineupSource, LineupStatus
from app.services.lineup_admission_gate import (
    LineupAdmissionGate,
    LineupAdmissionInput,
)
from app.services.verified_lineup import (
    LineupVerificationIssue,
    VerifiedFixtureLineups,
    VerifiedTeamLineupResult,
)

_NOW = datetime(2026, 8, 2, 18, 0, tzinfo=UTC)


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


def _verified_lineups(
    fixture_id: UUID,
    *,
    home_issues: tuple[LineupVerificationIssue, ...] = (),
    away_issues: tuple[LineupVerificationIssue, ...] = (),
) -> VerifiedFixtureLineups:
    home_team_id, away_team_id = uuid4(), uuid4()
    return VerifiedFixtureLineups(
        fixture_id=fixture_id,
        as_of=_NOW,
        home=VerifiedTeamLineupResult(
            team_id=home_team_id,
            lineup=None if home_issues else _lineup(fixture_id, home_team_id),
            issues=home_issues,
        ),
        away=VerifiedTeamLineupResult(
            team_id=away_team_id,
            lineup=None if away_issues else _lineup(fixture_id, away_team_id),
            issues=away_issues,
        ),
    )


@pytest.mark.unit
def test_initial_stage_accepts_without_lineup_result() -> None:
    decision = LineupAdmissionGate().evaluate(
        LineupAdmissionInput(
            fixture_id=uuid4(),
            stage=AnalysisStage.INITIAL,
            lineups=None,
        )
    )

    assert decision.approved
    assert decision.reasons == ("初始分析阶段不要求官方确认首发",)


@pytest.mark.unit
@pytest.mark.parametrize("stage", [AnalysisStage.POST_LINEUP, AnalysisStage.FINAL])
def test_required_stages_fail_closed_without_verification_result(stage: AnalysisStage) -> None:
    decision = LineupAdmissionGate().evaluate(
        LineupAdmissionInput(fixture_id=uuid4(), stage=stage, lineups=None)
    )

    assert not decision.approved
    assert "没有首发验证结果" in decision.reasons[0]


@pytest.mark.unit
@pytest.mark.parametrize("stage", [AnalysisStage.POST_LINEUP, AnalysisStage.FINAL])
def test_required_stages_accept_two_verified_lineups(stage: AnalysisStage) -> None:
    fixture_id = uuid4()
    decision = LineupAdmissionGate().evaluate(
        LineupAdmissionInput(
            fixture_id=fixture_id,
            stage=stage,
            lineups=_verified_lineups(fixture_id),
        )
    )

    assert decision.approved


@pytest.mark.unit
def test_collects_all_home_and_away_rejection_reasons() -> None:
    fixture_id = uuid4()
    lineups = _verified_lineups(
        fixture_id,
        home_issues=(LineupVerificationIssue.MISSING,),
        away_issues=(
            LineupVerificationIssue.FUTURE_SNAPSHOT,
            LineupVerificationIssue.EVIDENCE_BELOW_MINIMUM,
        ),
    )

    decision = LineupAdmissionGate().evaluate(
        LineupAdmissionInput(
            fixture_id=fixture_id,
            stage=AnalysisStage.FINAL,
            lineups=lineups,
        )
    )

    assert not decision.approved
    assert decision.reasons == (
        "主队首发验证未通过：missing",
        "客队首发验证未通过：future_snapshot",
        "客队首发验证未通过：evidence_below_minimum",
    )


@pytest.mark.unit
def test_rejects_lineups_belonging_to_another_fixture() -> None:
    decision = LineupAdmissionGate().evaluate(
        LineupAdmissionInput(
            fixture_id=uuid4(),
            stage=AnalysisStage.POST_LINEUP,
            lineups=_verified_lineups(uuid4()),
        )
    )

    assert not decision.approved
    assert decision.reasons[0] == "首发验证结果与当前比赛不匹配"


def test_input_rejects_untyped_stage() -> None:
    with pytest.raises(ValueError, match="AnalysisStage"):
        LineupAdmissionInput(
            fixture_id=uuid4(),
            stage=cast("AnalysisStage", "final"),
            lineups=None,
        )
