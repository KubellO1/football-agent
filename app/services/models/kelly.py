"""Kelly 准则下注单位计算。

给定模型概率与赔率，计算最优下注比例并产出 Stake。内建两重风控（宪法：
禁止重仓）：分数 Kelly 缩放，以及单注占 bankroll 的比例上限。无正 edge 时
返回 0 注。

公式（全 Kelly）：f* = (b·p − q) / b
    b = decimal_odds − 1（净赔率），p = 模型概率，q = 1 − p
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from app.models.value_objects.betting import Stake
from app.models.value_objects.money import Money

if TYPE_CHECKING:
    from app.models.value_objects.odds import Odds
    from app.models.value_objects.probability import Probability


class KellyCalculator:
    """分数 Kelly 计算器，带单注上限风控。"""

    def __init__(self, *, kelly_fraction: float = 0.25, max_fraction: float = 0.03) -> None:
        # kelly_fraction：分数 Kelly 系数（默认 1/4 Kelly，更抗模型误差）
        # max_fraction：单注占 bankroll 的上限（默认 3%）
        if not 0.0 < kelly_fraction <= 1.0:
            raise ValueError("kelly_fraction 必须在 (0, 1] 之间")
        if not 0.0 <= max_fraction <= 1.0:
            raise ValueError("max_fraction 必须在 [0, 1] 之间")
        self._kelly_fraction = kelly_fraction
        self._max_fraction = max_fraction

    def full_kelly_fraction(self, probability: Probability, odds: Odds) -> float:
        """全 Kelly 比例 f*；无正 edge 时为负，交由 compute 归零。"""
        p = probability.value
        b = float(odds.decimal) - 1.0
        if b <= 0:
            return 0.0
        q = 1.0 - p
        return (b * p - q) / b

    def compute(self, probability: Probability, odds: Odds, bankroll: Money) -> Stake:
        """计算建议下注，返回 Stake（含金额与占 bankroll 比例）。"""
        full = self.full_kelly_fraction(probability, odds)
        # 无正 edge 不下注
        if full <= 0.0:
            return Stake(amount=Money.zero(bankroll.currency), fraction_of_bankroll=0.0)

        # 分数 Kelly + 单注上限
        fraction = min(full * self._kelly_fraction, self._max_fraction)
        amount = bankroll * Decimal(str(fraction))
        return Stake(amount=amount, fraction_of_bankroll=fraction)
