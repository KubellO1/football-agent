"""日级 Top-N 选择服务。

在单场准入 gate（recommendation_gate）之上，做「当日」维度的收尾：对全部
候选中通过 gate 的部分排序，取当日最多 N 场（宪法第 8 节，默认 3），其余
给出放弃理由。

本服务是纯逻辑、不依赖 repository 或数学模型；它不产生任何数值，只做过滤、
排序与截断，并保留完整的取舍理由以供决策日志与每日报告使用。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.recommendation_gate import GateDecision, GateInput


@dataclass(frozen=True, slots=True)
class CandidateEvaluation:
    """一个候选投注及其准入评估结果。

    ``label`` 是比赛/投注的可读标识；排序所需的 EV 与综合评分从 ``gate_input``
    读取，避免重复存储。
    """

    label: str
    gate_input: GateInput
    gate_decision: GateDecision


@dataclass(frozen=True, slots=True)
class DailySelectionResult:
    """日级选择结果，分三类以便审计与报告。"""

    selected: list[CandidateEvaluation] = field(default_factory=list)  # 最终推荐（≤N）
    rejected: list[CandidateEvaluation] = field(default_factory=list)  # 未通过 gate
    over_limit: list[CandidateEvaluation] = field(default_factory=list)  # 通过但超出上限

    @property
    def has_recommendations(self) -> bool:
        """当日是否有推荐；为 False 时上层应输出「今天没有值得下注的比赛」。"""
        return len(self.selected) > 0


class DailySelectionService:
    """从当日候选中挑选最多 N 场推荐。"""

    def __init__(self, *, max_recommendations: int = 3) -> None:
        if max_recommendations < 0:
            raise ValueError("每日推荐上限不能为负数")
        self._max = max_recommendations

    @staticmethod
    def _sort_key(candidate: CandidateEvaluation) -> tuple[float, float]:
        """排序键：EV 优先、综合评分次之（均降序，见模块设计说明）。"""
        return (
            candidate.gate_input.expected_value,
            candidate.gate_input.decision_score.value,
        )

    def select(self, candidates: list[CandidateEvaluation]) -> DailySelectionResult:
        """过滤、排序、截断，返回分类结果。"""
        rejected = [c for c in candidates if not c.gate_decision.approved]
        approved = [c for c in candidates if c.gate_decision.approved]

        # sorted 是稳定排序；reverse=True 使 EV 与评分均按降序。
        ranked = sorted(approved, key=self._sort_key, reverse=True)

        selected = ranked[: self._max]
        over_limit = ranked[self._max :]

        return DailySelectionResult(
            selected=selected,
            rejected=rejected,
            over_limit=over_limit,
        )
