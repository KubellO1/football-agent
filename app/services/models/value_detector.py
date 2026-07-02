"""价值检测：模型概率 vs 市场隐含概率 → edge / EV / 价值判定。

宪法 EV 原则：不要只预测谁赢，而要比较模型概率与市场隐含概率，只有存在正
期望值(Positive EV)时才有价值。本模块产出 gate 所需的 EV 与价值判定，数值
全部由数学口径给出。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.value_objects.betting import ValueEdge
from app.models.value_objects.odds import Odds
from app.models.value_objects.probability import Probability


@dataclass(frozen=True, slots=True)
class ValueAssessment:
    """对单个投注项的价值评估结果（纯数值口径）。"""

    model_probability: Probability
    implied_probability: Probability  # 市场隐含概率（含庄家 margin）
    edge: float
    expected_value: float
    is_value: bool


class ValueDetector:
    """价值投注检测器。min_edge 为触发所需的最小 edge（默认 0，即任意正 EV）。"""

    def __init__(self, *, min_edge: float = 0.0) -> None:
        if min_edge < 0.0:
            raise ValueError("min_edge 不能为负数")
        self._min_edge = min_edge

    def assess(self, model_probability: Probability, odds: Odds) -> ValueAssessment:
        edge = ValueEdge(model_probability=model_probability, odds=odds)
        return ValueAssessment(
            model_probability=model_probability,
            implied_probability=odds.implied_probability,
            edge=edge.edge,
            expected_value=edge.expected_value_per_unit,
            is_value=edge.edge > self._min_edge,
        )
