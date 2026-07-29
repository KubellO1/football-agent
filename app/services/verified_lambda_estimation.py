"""仅对通过质量准入的 λ 输入执行数学估计。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.services.models.lambda_estimator import LambdaEstimator

if TYPE_CHECKING:
    from app.services.models.lambda_estimator import LambdaEstimate
    from app.services.verified_lambda_input import (
        LambdaInputComponent,
        VerifiedLambdaInputResult,
    )


@dataclass(frozen=True, slots=True)
class VerifiedLambdaEstimationResult:
    """可审计的 λ 执行结果；输入拒绝时不包含估计值。"""

    estimate: LambdaEstimate | None
    rejected_components: tuple[LambdaInputComponent, ...] = ()

    def __post_init__(self) -> None:
        if len(set(self.rejected_components)) != len(self.rejected_components):
            raise ValueError("rejected components cannot contain duplicates")
        if self.estimate is None and not self.rejected_components:
            raise ValueError("rejected estimation requires rejected components")
        if self.estimate is not None and self.rejected_components:
            raise ValueError("executed estimation cannot contain rejected components")

    @property
    def executed(self) -> bool:
        """数学估计器是否实际执行。"""
        return self.estimate is not None

    @property
    def usable(self) -> bool:
        """估计是否执行成功且未出现数据不足警告。"""
        return self.estimate is not None and not self.estimate.has_insufficient_data


class VerifiedLambdaEstimationService:
    """在质量准入与 LambdaEstimator 之间执行失败关闭。"""

    def __init__(self, *, estimator: LambdaEstimator | None = None) -> None:
        self._estimator = estimator or LambdaEstimator()

    def estimate(
        self,
        input_result: VerifiedLambdaInputResult,
    ) -> VerifiedLambdaEstimationResult:
        """拒绝未通过准入的输入，否则执行确定性 λ 估计。"""
        if not input_result.accepted:
            return VerifiedLambdaEstimationResult(
                estimate=None,
                rejected_components=input_result.rejected_components,
            )

        model_input = input_result.model_input
        if model_input is None:
            raise ValueError("accepted lambda input result must contain model input")

        return VerifiedLambdaEstimationResult(
            estimate=self._estimator.estimate(
                model_input.home_stats,
                model_input.away_stats,
                model_input.league,
            )
        )
