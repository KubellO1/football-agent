"""Kelly 计算器单元测试。

覆盖：全 Kelly 公式、无正 edge 归零、分数 Kelly 缩放、单注上限、金额计算、
以及构造参数校验。
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.config.settings import Settings
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
    # p=0.9, odds=2.0 → f*=0.8；全 Kelly 但上限 3%
    calc = KellyCalculator(kelly_fraction=1.0, max_fraction=0.03)
    stake = calc.compute(Probability(0.9), Odds(Decimal("2.0")), Money(Decimal("1000")))
    assert stake.fraction_of_bankroll == pytest.approx(0.03)


@pytest.mark.unit
def test_default_max_fraction_caps_values_above_three_percent() -> None:
    calc = KellyCalculator(kelly_fraction=1.0)
    stake = calc.compute(Probability(0.9), Odds(Decimal("2.0")), Money(Decimal("1000")))

    assert stake.fraction_of_bankroll == pytest.approx(0.03)
    assert stake.amount.amount == Decimal("30.000")


@pytest.mark.unit
def test_production_recommendation_defaults_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "RECOMMENDATIONS_MIN_EV",
        "RECOMMENDATIONS_MIN_CONFIDENCE",
        "RECOMMENDATIONS_MAX_STAKE_FRACTION",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=None)

    assert settings.recommendations_min_ev == pytest.approx(0.05)
    assert settings.recommendations_min_confidence == pytest.approx(0.70)
    assert settings.recommendations_max_stake_fraction == pytest.approx(0.03)


@pytest.mark.unit
def test_stake_amount_uses_bankroll_and_currency() -> None:
    calc = KellyCalculator(kelly_fraction=1.0, max_fraction=0.05)
    stake = calc.compute(Probability(0.9), Odds(Decimal("2.0")), Money(Decimal("1000"), "USD"))
    assert float(stake.amount.amount) == pytest.approx(50.0)  # 1000 * 0.05
    assert stake.amount.currency == "USD"


@pytest.mark.unit
@pytest.mark.parametrize(
    "kelly_fraction,max_fraction",
    [(0.0, 0.05), (1.5, 0.05), (0.25, -0.01), (0.25, 1.01)],
)
def test_invalid_params_rejected(kelly_fraction: float, max_fraction: float) -> None:
    with pytest.raises(ValueError):
        KellyCalculator(kelly_fraction=kelly_fraction, max_fraction=max_fraction)


@pytest.mark.unit
def test_zero_stake_cap_is_a_safe_no_bet_configuration() -> None:
    calc = KellyCalculator(kelly_fraction=1.0, max_fraction=0.0)

    stake = calc.compute(Probability(0.9), Odds(Decimal("2.0")), Money(Decimal("1000")))

    assert stake.fraction_of_bankroll == 0.0
    assert stake.amount.amount == Decimal("0.00")


@pytest.mark.unit
@pytest.mark.parametrize("value", [0.0, 0.01, 0.03])
def test_configured_stake_cap_accepts_production_range(value: float) -> None:
    settings = Settings(recommendations_max_stake_fraction=value)

    assert settings.recommendations_max_stake_fraction == pytest.approx(value)


@pytest.mark.unit
@pytest.mark.parametrize("value", [-0.000001, 0.030001, 0.05, 1.0])
def test_configured_stake_cap_rejects_values_outside_production_range(value: float) -> None:
    with pytest.raises(ValidationError):
        Settings(recommendations_max_stake_fraction=value)


@pytest.mark.unit
def test_configured_stake_cap_can_be_lower_than_three_percent() -> None:
    settings = Settings(recommendations_max_stake_fraction=0.01)
    calc = KellyCalculator(
        kelly_fraction=1.0,
        max_fraction=settings.recommendations_max_stake_fraction,
    )

    stake = calc.compute(Probability(0.9), Odds(Decimal("2.0")), Money(Decimal("1000")))

    assert stake.fraction_of_bankroll == pytest.approx(0.01)
