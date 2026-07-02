"""日级 Top-N 选择服务的单元测试。

覆盖：空输入、全部未通过、超额截断与排序、EV 相同按评分排序、
少于上限时全选、以及推荐为空的判定。
"""

from __future__ import annotations

import pytest

from app.models.value_objects.decision import (
    DataCompleteness,
    DecisionScore,
    EvidenceLevel,
    RiskLevel,
)
from app.services.daily_selection import CandidateEvaluation, DailySelectionService
from app.services.recommendation_gate import GateDecision, GateInput


def _candidate(label: str, *, ev: float, score: float, approved: bool) -> CandidateEvaluation:
    """构造一个候选评估；数据/证据/风险取达标值，仅按需要设定 approved。"""
    gate_input = GateInput(
        decision_score=DecisionScore(score),
        expected_value=ev,
        data_completeness=DataCompleteness(95.0),
        evidence_level=EvidenceLevel.B,
        risk_level=RiskLevel.MEDIUM,
    )
    return CandidateEvaluation(
        label=label,
        gate_input=gate_input,
        gate_decision=GateDecision(approved=approved, reasons=[]),
    )


@pytest.mark.unit
def test_empty_input_yields_empty_result() -> None:
    result = DailySelectionService().select([])
    assert result.selected == []
    assert result.rejected == []
    assert result.over_limit == []
    assert result.has_recommendations is False


@pytest.mark.unit
def test_all_rejected_produces_no_recommendations() -> None:
    candidates = [
        _candidate("A", ev=0.05, score=90, approved=False),
        _candidate("B", ev=0.08, score=88, approved=False),
    ]
    result = DailySelectionService().select(candidates)
    assert result.selected == []
    assert len(result.rejected) == 2
    assert result.has_recommendations is False


@pytest.mark.unit
def test_caps_at_three_and_ranks_by_ev_desc() -> None:
    candidates = [
        _candidate("low_ev", ev=0.01, score=99, approved=True),
        _candidate("top_ev", ev=0.20, score=86, approved=True),
        _candidate("mid_ev", ev=0.10, score=90, approved=True),
        _candidate("second_ev", ev=0.15, score=87, approved=True),
        _candidate("dropped", ev=0.02, score=95, approved=True),
    ]
    result = DailySelectionService().select(candidates)

    assert [c.label for c in result.selected] == ["top_ev", "second_ev", "mid_ev"]
    # over_limit 也按 EV 降序：dropped(0.02) 在 low_ev(0.01) 之前
    assert [c.label for c in result.over_limit] == ["dropped", "low_ev"]
    assert result.has_recommendations is True


@pytest.mark.unit
def test_ties_on_ev_break_by_score_desc() -> None:
    candidates = [
        _candidate("same_ev_low_score", ev=0.10, score=85, approved=True),
        _candidate("same_ev_high_score", ev=0.10, score=92, approved=True),
    ]
    result = DailySelectionService().select(candidates)
    assert [c.label for c in result.selected] == ["same_ev_high_score", "same_ev_low_score"]


@pytest.mark.unit
def test_fewer_than_limit_selects_all() -> None:
    candidates = [
        _candidate("A", ev=0.05, score=90, approved=True),
        _candidate("B", ev=0.08, score=88, approved=True),
    ]
    result = DailySelectionService().select(candidates)
    assert len(result.selected) == 2
    assert result.over_limit == []


@pytest.mark.unit
def test_custom_limit_is_respected() -> None:
    candidates = [_candidate(str(i), ev=0.1 * i, score=90, approved=True) for i in range(1, 5)]
    result = DailySelectionService(max_recommendations=1).select(candidates)
    assert len(result.selected) == 1
    assert result.selected[0].label == "4"  # EV 最高
    assert len(result.over_limit) == 3
