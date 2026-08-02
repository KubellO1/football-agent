"""阶段感知的首发阵容准入 Gate。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.models.value_objects.analysis_stage import AnalysisStage

if TYPE_CHECKING:
    from uuid import UUID

    from app.services.verified_lineup import VerifiedFixtureLineups, VerifiedTeamLineupResult


@dataclass(frozen=True, slots=True)
class LineupAdmissionInput:
    """一场比赛在明确分析阶段下的首发准入输入。"""

    fixture_id: UUID
    stage: AnalysisStage
    lineups: VerifiedFixtureLineups | None

    def __post_init__(self) -> None:
        if not isinstance(self.stage, AnalysisStage):
            raise ValueError("stage must be an AnalysisStage member")


@dataclass(frozen=True, slots=True)
class LineupAdmissionDecision:
    """首发准入结论及可写入决策日志的完整原因。"""

    approved: bool
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.reasons:
            raise ValueError("lineup admission decision requires at least one reason")


class LineupAdmissionGate:
    """只做风险准入，不修改概率、EV、Kelly 或任何数学模型输出。"""

    def evaluate(self, data: LineupAdmissionInput) -> LineupAdmissionDecision:
        """按显式阶段执行首发硬约束，并收集全部拒绝原因。"""
        if not data.stage.requires_confirmed_lineups:
            return LineupAdmissionDecision(
                approved=True,
                reasons=("初始分析阶段不要求官方确认首发",),
            )

        if data.lineups is None:
            return LineupAdmissionDecision(
                approved=False,
                reasons=("当前分析阶段要求官方确认首发，但没有首发验证结果",),
            )

        failures: list[str] = []
        if data.lineups.fixture_id != data.fixture_id:
            failures.append("首发验证结果与当前比赛不匹配")

        failures.extend(self._team_failures("主队", data.lineups.home))
        failures.extend(self._team_failures("客队", data.lineups.away))
        if failures:
            return LineupAdmissionDecision(approved=False, reasons=tuple(failures))

        return LineupAdmissionDecision(
            approved=True,
            reasons=("主客队官方确认首发均已通过时间、来源和证据验证",),
        )

    @staticmethod
    def _team_failures(label: str, result: VerifiedTeamLineupResult) -> list[str]:
        if result.accepted:
            return []
        if not result.issues:
            return [f"{label}首发验证未通过且缺少拒绝原因"]
        return [f"{label}首发验证未通过：{issue.value}" for issue in result.issues]
