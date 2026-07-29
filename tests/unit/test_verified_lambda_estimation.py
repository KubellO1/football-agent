"""VerifiedLambdaEstimationService 的执行边界与可用性单元测试。"""

from __future__ import annotations

from typing import cast
from unittest.mock import MagicMock

import pytest

from app.models.value_objects.statistics import TeamStatistics
from app.services.models.lambda_estimator import (
    BaselineMetric,
    LambdaEstimate,
    LambdaEstimator,
    LambdaWarning,
    LambdaWarningType,
    LeagueBaseline,
)
from app.services.verified_lambda_estimation import (
    VerifiedLambdaEstimationResult,
    VerifiedLambdaEstimationService,
)
from app.services.verified_lambda_input import (
    LambdaInputComponent,
    VerifiedLambdaInput,
    VerifiedLambdaInputResult,
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


def _accepted_input() -> VerifiedLambdaInputResult:
    return VerifiedLambdaInputResult(
        model_input=VerifiedLambdaInput(
            home_stats=_statistics(xg_for=16.0, xg_against=10.0),
            away_stats=_statistics(xg_for=12.0, xg_against=14.0),
            league=LeagueBaseline(
                rate_per_team_match=1.35,
                metric=BaselineMetric.XG,
            ),
        )
    )


def _service_with_result(
    estimate: LambdaEstimate,
) -> tuple[VerifiedLambdaEstimationService, MagicMock]:
    estimator = MagicMock(spec=LambdaEstimator)
    estimator.estimate.return_value = estimate
    service = VerifiedLambdaEstimationService(
        estimator=cast("LambdaEstimator", estimator),
    )
    return service, estimator


@pytest.mark.unit
def test_estimate_executes_model_once_with_verified_inputs() -> None:
    expected = LambdaEstimate(lam_home=1.8, lam_away=0.9)
    service, estimator = _service_with_result(expected)
    input_result = _accepted_input()
    assert input_result.model_input is not None

    result = service.estimate(input_result)

    assert result.estimate is expected
    assert result.executed
    assert result.usable
    assert result.rejected_components == ()
    estimator.estimate.assert_called_once_with(
        input_result.model_input.home_stats,
        input_result.model_input.away_stats,
        input_result.model_input.league,
    )


@pytest.mark.unit
def test_rejected_input_never_executes_model_and_preserves_components() -> None:
    service, estimator = _service_with_result(LambdaEstimate(lam_home=1.0, lam_away=1.0))
    rejected = (
        LambdaInputComponent.HOME_FORM,
        LambdaInputComponent.LEAGUE_BASELINE,
    )
    input_result = VerifiedLambdaInputResult(
        model_input=None,
        rejected_components=rejected,
    )

    result = service.estimate(input_result)

    assert result.estimate is None
    assert not result.executed
    assert not result.usable
    assert result.rejected_components == rejected
    estimator.estimate.assert_not_called()


@pytest.mark.unit
def test_insufficient_data_warning_marks_executed_result_unusable() -> None:
    warning = LambdaWarning(
        team="home",
        raw_lambda=0.0,
        warning_type=LambdaWarningType.INSUFFICIENT_DATA,
        reason="insufficient scoring history",
    )
    expected = LambdaEstimate(
        lam_home=0.05,
        lam_away=1.0,
        warnings=[warning],
    )
    service, _ = _service_with_result(expected)

    result = service.estimate(_accepted_input())

    assert result.executed
    assert not result.usable
    assert result.estimate is expected
    assert result.estimate.warnings == [warning]


@pytest.mark.unit
def test_genuine_low_warning_remains_usable() -> None:
    warning = LambdaWarning(
        team="away",
        raw_lambda=0.03,
        warning_type=LambdaWarningType.GENUINE_LOW,
        reason="genuine low-scoring estimate",
    )
    expected = LambdaEstimate(
        lam_home=1.2,
        lam_away=0.05,
        warnings=[warning],
    )
    service, _ = _service_with_result(expected)

    result = service.estimate(_accepted_input())

    assert result.executed
    assert result.usable
    assert result.estimate is expected


@pytest.mark.unit
@pytest.mark.parametrize(
    ("estimate", "rejected_components", "message"),
    [
        (None, (), "requires rejected components"),
        (
            LambdaEstimate(lam_home=1.0, lam_away=1.0),
            (LambdaInputComponent.AWAY_FORM,),
            "cannot contain rejected components",
        ),
        (
            None,
            (
                LambdaInputComponent.HOME_FORM,
                LambdaInputComponent.HOME_FORM,
            ),
            "cannot contain duplicates",
        ),
    ],
)
def test_result_rejects_contradictory_states(
    estimate: LambdaEstimate | None,
    rejected_components: tuple[LambdaInputComponent, ...],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        VerifiedLambdaEstimationResult(
            estimate=estimate,
            rejected_components=rejected_components,
        )
