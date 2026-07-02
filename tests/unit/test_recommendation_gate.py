"""推荐准入 gate 的单元测试。

覆盖：全部满足、各单项门槛失败、风控优先一票否决、以及多项失败时的
原因收集。验证宪法第 8 节门槛与第 2.3 节风控优先原则。
"""

from __future__ import annotations

import pytest

from app.models.value_objects.decision import (
    DataCompleteness,
    DecisionScore,
    EvidenceLevel,
    RiskLevel,
)
from app.services.recommendation_gate import GateInput, RecommendationGate


def _passing_input(**overrides: object) -> GateInput:
    """构造一个默认全部达标的输入，可通过 overrides 覆盖单项。"""
    base: dict[str, object] = {
        "decision_score": DecisionScore(88.0),
        "expected_value": 0.06,
        "data_completeness": DataCompleteness(95.0),
        "evidence_level": EvidenceLevel.B,
        "risk_level": RiskLevel.MEDIUM,
    }
    base.update(overrides)
    return GateInput(**base)  # type: ignore[arg-type]


@pytest.mark.unit
def test_all_criteria_met_approves() -> None:
    decision = RecommendationGate().evaluate(_passing_input())
    assert decision.approved is True
    assert decision.reasons  # 通过时也应给出说明


@pytest.mark.unit
def test_insufficient_data_completeness_rejects() -> None:
    decision = RecommendationGate().evaluate(
        _passing_input(data_completeness=DataCompleteness(80.0))
    )
    assert decision.approved is False
    assert any("数据完整度" in r for r in decision.reasons)


@pytest.mark.unit
def test_evidence_below_b_rejects() -> None:
    decision = RecommendationGate().evaluate(_passing_input(evidence_level=EvidenceLevel.C))
    assert decision.approved is False
    assert any("证据等级" in r for r in decision.reasons)


@pytest.mark.unit
@pytest.mark.parametrize("ev", [0.0, -0.05])
def test_non_positive_ev_rejects(ev: float) -> None:
    decision = RecommendationGate().evaluate(_passing_input(expected_value=ev))
    assert decision.approved is False
    assert any("期望值" in r for r in decision.reasons)


@pytest.mark.unit
def test_score_below_threshold_rejects() -> None:
    decision = RecommendationGate().evaluate(_passing_input(decision_score=DecisionScore(84.0)))
    assert decision.approved is False
    assert any("综合评分" in r for r in decision.reasons)


@pytest.mark.unit
def test_high_risk_vetoes_even_when_value_and_score_are_strong() -> None:
    # 价值与预测都很强，但风险为高 —— 依据风控优先必须拒绝。
    decision = RecommendationGate().evaluate(
        _passing_input(
            decision_score=DecisionScore(99.0),
            expected_value=0.20,
            risk_level=RiskLevel.HIGH,
        )
    )
    assert decision.approved is False
    assert any("风险等级为「高」" in r for r in decision.reasons)
    assert any("优先风控" in r for r in decision.reasons)  # 冲突裁决被显式记录


@pytest.mark.unit
def test_multiple_failures_are_all_collected() -> None:
    # 同时违反完整度、证据、EV、评分 —— gate 不短路，应收集全部原因。
    decision = RecommendationGate().evaluate(
        _passing_input(
            decision_score=DecisionScore(50.0),
            expected_value=-0.10,
            data_completeness=DataCompleteness(40.0),
            evidence_level=EvidenceLevel.D,
        )
    )
    assert decision.approved is False
    assert len(decision.reasons) >= 4
