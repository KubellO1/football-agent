"""Kelly 计算器单元测试。

覆盖：全 Kelly 公式、无正 edge 归零、分数 Kelly 缩放、单注上限、金额计算、
以及构造参数校验。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.models.value_objects.money import Money
from app.models.value_objects.odds import Odds
from app.models.value_objects.probability import Probability
from app.services.models.kelly import KellyCalculator


@pytest.mark.unit
def test_full_kelly_fraction_positive_edge() -> None:
    # p=0.6, odds=2.0 → b=1, f*=(1*0.6-0.4)/1=0.2
    calc = KellyCalculator(kelly_fraction=1.0, max_fraction=1.0)
    f = calc.full_kelly_fraction(Probability(0.6), Odds(Decimal("2.0")))
    assert f == pytest.approx(0.2)


@pytest.mark.unit
def test_no_positive_edge_returns_zero_stake() -> None:
    # p=0.4, odds=2.0 → f*=(0.4-0.6)/1=-0.2 → 不下注
    calc = KellyCalculator(kelly_fraction=1.0, max_fraction=1.0)
    stake = calc.compute(Probability(0.4), Odds(Decimal("2.0")), Money(Decimal("1000")))
    assert stake.fraction_of_bankroll == 0.0
    assert stake.amount.amount == Decimal("0")


@pytest.mark.unit
def test_fractional_kelly_scales_down() -> None:
    # 全 Kelly 0.2，1/4 Kelly → 0.05
    calc = KellyCalculator(kelly_fraction=0.25, max_fraction=1.0)
    stake = calc.compute(Probability(0.6), Odds(Decimal("2.0")), Money(Decimal("1000")))
    assert stake.fraction_of_bankroll == pytest.approx(0.05)


@pytest.mark.unit
def test_max_fraction_caps_stake() -> None:
    # p=0.9, odds=2.0 → f*=0.8；全 Kelly 但上限 5%
    calc = KellyCalculator(kelly_fraction=1.0, max_fraction=0.05)
    stake = calc.compute(Probability(0.9), Odds(Decimal("2.0")), Money(Decimal("1000")))
    assert stake.fraction_of_bankroll == pytest.approx(0.05)


@pytest.mark.unit
def test_stake_amount_uses_bankroll_and_currency() -> None:
    calc = KellyCalculator(kelly_fraction=1.0, max_fraction=0.05)
    stake = calc.compute(Probability(0.9), Odds(Decimal("2.0")), Money(Decimal("1000"), "USD"))
    assert float(stake.amount.amount) == pytest.approx(50.0)  # 1000 * 0.05
    assert stake.amount.currency == "USD"


@pytest.mark.unit
@pytest.mark.parametrize("kelly_fraction,max_fraction", [(0.0, 0.05), (1.5, 0.05), (0.25, 0.0)])
def test_invalid_params_rejected(kelly_fraction: float, max_fraction: float) -> None:
    with pytest.raises(ValueError):
        KellyCalculator(kelly_fraction=kelly_fraction, max_fraction=max_fraction)
