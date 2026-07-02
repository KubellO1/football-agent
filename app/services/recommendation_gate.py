"""推荐准入 gate。

把系统宪法（docs/agent-constitution.md）的准入门槛（第 8 节）与最高优先级
规则（第 2.3 节：预测/盘口/风险冲突时优先风控）编码为确定性、可审计的纯逻辑。

这是「钱的安全边界」，必须是代码硬约束，而不是提示词层面的建议。gate 不做
任何预测、不产生数值——它只依据已有的量化结论与风险评估，判定单场比赛的某个
候选投注是否满足推荐条件，并给出可写入决策日志的完整理由。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.value_objects.decision import (
    DataCompleteness,
    DecisionScore,
    EvidenceLevel,
    RiskLevel,
)


@dataclass(frozen=True, slots=True)
class GateInput:
    """单场候选投注的准入评估输入。数值均来自量化模型 / 数据层。"""

    decision_score: DecisionScore
    expected_value: float
    data_completeness: DataCompleteness
    evidence_level: EvidenceLevel
    risk_level: RiskLevel


@dataclass(frozen=True, slots=True)
class GateDecision:
    """准入判定结果。approved 为最终结论，reasons 记录全部依据（中文）。"""

    approved: bool
    reasons: list[str] = field(default_factory=list)


class RecommendationGate:
    """依据宪法门槛与风控优先原则，判定候选投注是否可推荐。"""

    def __init__(
        self,
        *,
        min_decision_score: float = 85.0,
        min_data_completeness: float = 90.0,
        min_evidence_level: EvidenceLevel = EvidenceLevel.B,
    ) -> None:
        self._min_decision_score = min_decision_score
        self._min_data_completeness = min_data_completeness
        self._min_evidence_level = min_evidence_level

    def evaluate(self, data: GateInput) -> GateDecision:
        """评估单个候选投注是否满足全部准入条件。

        不短路：收集所有未通过项，便于写入决策日志。风险为「高」一票否决，
        体现风控优先——即使价值与评分都达标也不推荐。
        """
        failures: list[str] = []

        # 门槛 1：数据完整度 ≥ 90%
        completeness_ok = data.data_completeness.is_sufficient(self._min_data_completeness)
        if not completeness_ok:
            failures.append(
                f"数据完整度 {data.data_completeness.value:.1f}% "
                f"低于 {self._min_data_completeness:.0f}%"
            )

        # 门槛 2：证据等级 ≥ B
        evidence_ok = data.evidence_level.meets_minimum(self._min_evidence_level)
        if not evidence_ok:
            failures.append(
                f"证据等级 {data.evidence_level.value} 低于 {self._min_evidence_level.value} 级"
            )

        # 门槛 3：EV > 0（正期望值）
        value_ok = data.expected_value > 0.0
        if not value_ok:
            failures.append(f"期望值 EV={data.expected_value:.3f} ≤ 0，无正期望值")

        # 门槛 4：综合评分 ≥ 85
        score_ok = data.decision_score.is_recommendable(self._min_decision_score)
        if not score_ok:
            failures.append(
                f"综合评分 {data.decision_score.value:.1f} 低于 {self._min_decision_score:.0f}"
            )

        # 最高优先级规则：风险为「高」一票否决（风控优先）
        risk_ok = data.risk_level is not RiskLevel.HIGH
        if not risk_ok:
            failures.append("风险等级为「高」，依据风控优先原则放弃")

        # 冲突检测：价值与预测均达标，但风险不可接受 → 显式记录冲突裁决
        if value_ok and score_ok and completeness_ok and evidence_ok and not risk_ok:
            failures.append("检测到价值/预测与风险冲突，依据最高优先级规则优先风控")

        if failures:
            return GateDecision(approved=False, reasons=failures)

        return GateDecision(
            approved=True,
            reasons=["满足全部准入条件：数据完整度、证据等级、正 EV、综合评分、风险可接受"],
        )
