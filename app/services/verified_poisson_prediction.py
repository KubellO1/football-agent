"""仅对可用的已验证 λ 结果执行 Poisson 胜平负预测。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.services.models.poisson import PoissonModel

if TYPE_CHECKING:
    from app.models.value_objects.probability import Probability
    from app.models.value_objects.score import MatchResult
    from app.services.verified_lambda_estimation import VerifiedLambdaEstimationResult


@dataclass(frozen=True, slots=True)
class VerifiedPoissonPredictionResult:
    """Poisson 预测及其完整上游 λ 审计结果。"""

    estimation: VerifiedLambdaEstimationResult
    outcome_probabilities: dict[MatchResult, Probability] | None

    def __post_init__(self) -> None:
        has_prediction = self.outcome_probabilities is not None
        if self.estimation.usable != has_prediction:
            raise ValueError("Poisson prediction presence must match lambda usability")

    @property
    def predicted(self) -> bool:
        """Poisson 模型是否实际执行并生成概率。"""
        return self.outcome_probabilities is not None


class VerifiedPoissonPredictionService:
    """在验证式 λ 结果与 PoissonModel 之间执行失败关闭。"""

    def __init__(self, *, poisson: PoissonModel | None = None) -> None:
        self._poisson = poisson or PoissonModel()

    def predict(
        self,
        estimation: VerifiedLambdaEstimationResult,
    ) -> VerifiedPoissonPredictionResult:
        """只对可用 λ 执行一次胜平负概率计算。"""
        if not estimation.usable:
            return VerifiedPoissonPredictionResult(
                estimation=estimation,
                outcome_probabilities=None,
            )

        estimate = estimation.estimate
        if estimate is None:
            raise ValueError("usable lambda estimation must contain an estimate")

        return VerifiedPoissonPredictionResult(
            estimation=estimation,
            outcome_probabilities=self._poisson.match_result_probabilities(
                estimate.lam_home,
                estimate.lam_away,
            ),
        )
