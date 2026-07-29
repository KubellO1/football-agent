"""VerifiedLambdaInputBuilder 的失败关闭与输入组装单元测试。"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.models.value_objects.decision import DataCompleteness, EvidenceLevel
from app.models.value_objects.statistics import TeamStatistics
from app.services.models.lambda_estimator import BaselineMetric
from app.services.verified_lambda_input import (
    LambdaInputComponent,
    VerifiedLambdaInputBuilder,
)
from app.services.verified_league_baseline import (
    LeagueBaselineStatus,
    LeagueXGBaseline,
    VerifiedLeagueBaselineResult,
)
from app.services.verified_team_form import (
    TeamFormStatus,
    VerifiedTeamFormResult,
)


def _statistics(*, xg_for: float, xg_against: float) -> TeamStatistics:
    return TeamStatistics(
        matches_played=10,
        wins=5,
        draws=3,
        losses=2,
        goals_for=16,
        goals_against=10,
        xg_for=xg_for,
        xg_against=xg_against,
    )


def _verified_form(statistics: TeamStatistics) -> VerifiedTeamFormResult:
    return VerifiedTeamFormResult(
        status=TeamFormStatus.VERIFIED,
        statistics=statistics,
        data_completeness=DataCompleteness(96.0),
        evidence_level=EvidenceLevel.B,
        requested_window=10,
        history_count=10,
        verified_count=10,
    )


def _rejected_form() -> VerifiedTeamFormResult:
    return VerifiedTeamFormResult(
        status=TeamFormStatus.QUALITY_REJECTED,
        statistics=None,
        data_completeness=DataCompleteness(90.0),
        evidence_level=EvidenceLevel.B,
        requested_window=10,
        history_count=10,
        verified_count=9,
        rejected_fixture_ids=(uuid4(),),
    )


def _verified_baseline() -> VerifiedLeagueBaselineResult:
    return VerifiedLeagueBaselineResult(
        status=LeagueBaselineStatus.VERIFIED,
        baseline=LeagueXGBaseline(
            xg_per_team_match=1.4,
            fixture_count=30,
        ),
        data_completeness=DataCompleteness(95.0),
        evidence_level=EvidenceLevel.B,
        maximum_fixtures=100,
        minimum_fixtures=30,
        history_count=30,
        verified_fixture_count=30,
    )


def _rejected_baseline() -> VerifiedLeagueBaselineResult:
    return VerifiedLeagueBaselineResult(
        status=LeagueBaselineStatus.QUALITY_REJECTED,
        baseline=None,
        data_completeness=DataCompleteness(96.0),
        evidence_level=EvidenceLevel.B,
        maximum_fixtures=100,
        minimum_fixtures=30,
        history_count=30,
        verified_fixture_count=29,
        rejected_fixture_ids=(uuid4(),),
    )


@pytest.mark.unit
def test_build_combines_verified_components_into_explicit_xg_input() -> None:
    home_stats = _statistics(xg_for=16.2, xg_against=9.8)
    away_stats = _statistics(xg_for=13.4, xg_against=12.1)

    result = VerifiedLambdaInputBuilder().build(
        home_form=_verified_form(home_stats),
        away_form=_verified_form(away_stats),
        league_baseline=_verified_baseline(),
    )

    assert result.accepted
    assert result.rejected_components == ()
    assert result.model_input is not None
    assert result.model_input.home_stats is home_stats
    assert result.model_input.away_stats is away_stats
    assert result.model_input.league.rate_per_team_match == pytest.approx(1.4)
    assert result.model_input.league.metric is BaselineMetric.XG


@pytest.mark.unit
@pytest.mark.parametrize(
    ("reject_home", "reject_away", "reject_league", "expected"),
    [
        (True, False, False, (LambdaInputComponent.HOME_FORM,)),
        (False, True, False, (LambdaInputComponent.AWAY_FORM,)),
        (False, False, True, (LambdaInputComponent.LEAGUE_BASELINE,)),
        (
            True,
            True,
            True,
            (
                LambdaInputComponent.HOME_FORM,
                LambdaInputComponent.AWAY_FORM,
                LambdaInputComponent.LEAGUE_BASELINE,
            ),
        ),
    ],
)
def test_build_fails_closed_and_records_every_rejected_component(
    reject_home: bool,
    reject_away: bool,
    reject_league: bool,
    expected: tuple[LambdaInputComponent, ...],
) -> None:
    statistics = _statistics(xg_for=14.0, xg_against=12.0)

    result = VerifiedLambdaInputBuilder().build(
        home_form=_rejected_form() if reject_home else _verified_form(statistics),
        away_form=_rejected_form() if reject_away else _verified_form(statistics),
        league_baseline=_rejected_baseline() if reject_league else _verified_baseline(),
    )

    assert not result.accepted
    assert result.model_input is None
    assert result.rejected_components == expected
