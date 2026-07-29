"""VerifiedPoissonPredictionService 的失败关闭与审计保留单元测试。"""

from __future__ import annotations

from typing import cast
from unittest.mock import MagicMock

import pytest

from app.models.value_objects.probability import Probability
from app.models.value_objects.score import MatchResult
from app.services.models.lambda_estimator import (
    LambdaEstimate,
    LambdaWarning,
    LambdaWarningType,
)
from app.services.models.poisson import PoissonModel
from app.services.verified_lambda_estimation import VerifiedLambdaEstimationResult
from app.services.verified_lambda_input import LambdaInputComponent
from app.services.verified_poisson_prediction import (
    VerifiedPoissonPredictionResult,
    VerifiedPoissonPredictionService,
)


def _probabilities() -> dict[MatchResult, Probability]:
    return {
        MatchResult.HOME: Probability(0.5),
        MatchResult.DRAW: Probability(0.3),
        MatchResult.AWAY: Probability(0.2),
    }


def _service_with_probabilities(
    probabilities: dict[MatchResult, Probability],
) -> tuple[VerifiedPoissonPredictionService, MagicMock]:
    poisson = MagicMock(spec=PoissonModel)
    poisson.match_result_probabilities.return_value = probabilities
    service = VerifiedPoissonPredictionService(
        poisson=cast("PoissonModel", poisson),
    )
    return service, poisson


@pytest.mark.unit
def test_predict_executes_poisson_once_for_usable_lambda() -> None:
    estimation = VerifiedLambdaEstimationResult(estimate=LambdaEstimate(lam_home=1.7, lam_away=1.1))
    expected = _probabilities()
    service, poisson = _service_with_probabilities(expected)

    result = service.predict(estimation)

    assert result.predicted
    assert result.estimation is estimation
    assert result.outcome_probabilities is expected
    poisson.match_result_probabilities.assert_called_once_with(1.7, 1.1)


@pytest.mark.unit
def test_rejected_lambda_input_never_executes_poisson() -> None:
    estimation = VerifiedLambdaEstimationResult(
        estimate=None,
        rejected_components=(
            LambdaInputComponent.HOME_FORM,
            LambdaInputComponent.LEAGUE_BASELINE,
        ),
    )
    service, poisson = _service_with_probabilities(_probabilities())

    result = service.predict(estimation)

    assert not result.predicted
    assert result.outcome_probabilities is None
    assert result.estimation is estimation
    assert result.estimation.rejected_components == estimation.rejected_components
    poisson.match_result_probabilities.assert_not_called()


@pytest.mark.unit
def test_insufficient_data_lambda_never_executes_poisson() -> None:
    warning = LambdaWarning(
        team="home",
        raw_lambda=0.0,
        warning_type=LambdaWarningType.INSUFFICIENT_DATA,
        reason="insufficient scoring history",
    )
    estimation = VerifiedLambdaEstimationResult(
        estimate=LambdaEstimate(
            lam_home=0.05,
            lam_away=1.0,
            warnings=[warning],
        )
    )
    service, poisson = _service_with_probabilities(_probabilities())

    result = service.predict(estimation)

    assert not result.predicted
    assert result.estimation.estimate is estimation.estimate
    assert result.estimation.estimate is not None
    assert result.estimation.estimate.warnings == [warning]
    poisson.match_result_probabilities.assert_not_called()


@pytest.mark.unit
def test_genuine_low_lambda_still_executes_poisson() -> None:
    warning = LambdaWarning(
        team="away",
        raw_lambda=0.03,
        warning_type=LambdaWarningType.GENUINE_LOW,
        reason="genuine low-scoring estimate",
    )
    estimation = VerifiedLambdaEstimationResult(
        estimate=LambdaEstimate(
            lam_home=1.2,
            lam_away=0.05,
            warnings=[warning],
        )
    )
    service, poisson = _service_with_probabilities(_probabilities())

    result = service.predict(estimation)

    assert result.predicted
    assert result.estimation.estimate is estimation.estimate
    poisson.match_result_probabilities.assert_called_once_with(1.2, 0.05)


@pytest.mark.unit
def test_poisson_error_is_not_disguised_as_quality_rejection() -> None:
    poisson = MagicMock(spec=PoissonModel)
    poisson.match_result_probabilities.side_effect = ValueError("invalid model state")
    service = VerifiedPoissonPredictionService(
        poisson=cast("PoissonModel", poisson),
    )
    estimation = VerifiedLambdaEstimationResult(estimate=LambdaEstimate(lam_home=1.4, lam_away=1.0))

    with pytest.raises(ValueError, match="invalid model state"):
        service.predict(estimation)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("estimation", "probabilities"),
    [
        (
            VerifiedLambdaEstimationResult(estimate=LambdaEstimate(lam_home=1.4, lam_away=1.0)),
            None,
        ),
        (
            VerifiedLambdaEstimationResult(
                estimate=None,
                rejected_components=(LambdaInputComponent.AWAY_FORM,),
            ),
            _probabilities(),
        ),
    ],
)
def test_result_rejects_prediction_that_conflicts_with_lambda_usability(
    estimation: VerifiedLambdaEstimationResult,
    probabilities: dict[MatchResult, Probability] | None,
) -> None:
    with pytest.raises(ValueError, match="presence must match"):
        VerifiedPoissonPredictionResult(
            estimation=estimation,
            outcome_probabilities=probabilities,
        )
