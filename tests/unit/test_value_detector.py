"""ValueDetector 单元测试。

覆盖：正 edge 判定为价值、无 edge 不判价值、edge=0 边界、min_edge 阈值、
市场隐含概率计算、非法参数。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.models.value_objects.odds import Odds
from app.models.value_objects.probability import Probability
from app.services.models.value_detector import ValueDetector


@pytest.mark.unit
def test_positive_edge_is_value() -> None:
    # p=0.6, odds=2.0 → edge = 0.6*2 - 1 = 0.2
    result = ValueDetector().assess(Probability(0.6), Odds(Decimal("2.0")))
    assert result.edge == pytest.approx(0.2)
    assert result.expected_value == pytest.approx(0.2)
    assert result.is_value is True
    assert result.implied_probability.value == pytest.approx(0.5)


@pytest.mark.unit
def test_negative_edge_is_not_value() -> None:
    # p=0.4, odds=2.0 → edge = -0.2
    result = ValueDetector().assess(Probability(0.4), Odds(Decimal("2.0")))
    assert result.edge == pytest.approx(-0.2)
    assert result.is_value is False


@pytest.mark.unit
def test_zero_edge_is_not_value() -> None:
    # p=0.5, odds=2.0 → edge = 0.0，不构成价值（需严格 > min_edge）
    result = ValueDetector().assess(Probability(0.5), Odds(Decimal("2.0")))
    assert result.edge == pytest.approx(0.0)
    assert result.is_value is False


@pytest.mark.unit
def test_min_edge_threshold() -> None:
    # edge=0.2：阈值 0.25 不算价值；阈值 0.1 算价值
    strict = ValueDetector(min_edge=0.25).assess(Probability(0.6), Odds(Decimal("2.0")))
    lenient = ValueDetector(min_edge=0.1).assess(Probability(0.6), Odds(Decimal("2.0")))
    assert strict.is_value is False
    assert lenient.is_value is True


@pytest.mark.unit
def test_implied_probability_from_odds() -> None:
    result = ValueDetector().assess(Probability(0.3), Odds(Decimal("4.0")))
    assert result.implied_probability.value == pytest.approx(0.25)


@pytest.mark.unit
def test_negative_min_edge_rejected() -> None:
    with pytest.raises(ValueError):
        ValueDetector(min_edge=-0.1)
