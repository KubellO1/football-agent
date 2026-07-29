"""组装通过质量准入的主客队状态与联赛基准。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from app.services.verified_league_baseline_adapter import VerifiedLeagueBaselineAdapter

if TYPE_CHECKING:
    from app.models.value_objects.statistics import TeamStatistics
    from app.services.models.lambda_estimator import LeagueBaseline
    from app.services.verified_league_baseline import VerifiedLeagueBaselineResult
    from app.services.verified_team_form import VerifiedTeamFormResult


class LambdaInputComponent(StrEnum):
    """构建 λ 输入所需的独立数据组件。"""

    HOME_FORM = "home_form"
    AWAY_FORM = "away_form"
    LEAGUE_BASELINE = "league_baseline"


@dataclass(frozen=True, slots=True)
class VerifiedLambdaInput:
    """可直接交给 LambdaEstimator 的已验证输入。"""

    home_stats: TeamStatistics
    away_stats: TeamStatistics
    league: LeagueBaseline


@dataclass(frozen=True, slots=True)
class VerifiedLambdaInputResult:
    """可审计的组装结果；拒绝时不暴露部分输入。"""

    model_input: VerifiedLambdaInput | None
    rejected_components: tuple[LambdaInputComponent, ...] = ()

    def __post_init__(self) -> None:
        if len(set(self.rejected_components)) != len(self.rejected_components):
            raise ValueError("rejected components cannot contain duplicates")
        if self.model_input is None and not self.rejected_components:
            raise ValueError("rejected result requires at least one rejected component")
        if self.model_input is not None and self.rejected_components:
            raise ValueError("accepted result cannot contain rejected components")

    @property
    def accepted(self) -> bool:
        return self.model_input is not None


class VerifiedLambdaInputBuilder:
    """仅在全部上游组件通过准入后组装 λ 输入。"""

    def __init__(
        self,
        *,
        baseline_adapter: VerifiedLeagueBaselineAdapter | None = None,
    ) -> None:
        self._baseline_adapter = baseline_adapter or VerifiedLeagueBaselineAdapter()

    def build(
        self,
        *,
        home_form: VerifiedTeamFormResult,
        away_form: VerifiedTeamFormResult,
        league_baseline: VerifiedLeagueBaselineResult,
    ) -> VerifiedLambdaInputResult:
        """收集所有拒绝组件，并在任一失败时停止组装。"""
        rejected: list[LambdaInputComponent] = []
        if not home_form.accepted:
            rejected.append(LambdaInputComponent.HOME_FORM)
        if not away_form.accepted:
            rejected.append(LambdaInputComponent.AWAY_FORM)
        if not league_baseline.accepted:
            rejected.append(LambdaInputComponent.LEAGUE_BASELINE)

        if rejected:
            return VerifiedLambdaInputResult(
                model_input=None,
                rejected_components=tuple(rejected),
            )

        if home_form.statistics is None or away_form.statistics is None:
            raise ValueError("accepted team form must contain statistics")

        return VerifiedLambdaInputResult(
            model_input=VerifiedLambdaInput(
                home_stats=home_form.statistics,
                away_stats=away_form.statistics,
                league=self._baseline_adapter.adapt(league_baseline),
            )
        )
