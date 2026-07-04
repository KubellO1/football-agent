"""概率校准层：温度缩放（temperature scaling）。

回测校准曲线显示模型在高概率端**过度自信**（预测 90% 的事件实际只发生约 68%）。
温度缩放用单参数 T 重整概率分布：

    p_i' = p_i^(1/T) / Σ_j p_j^(1/T)

T=1 为恒等；T>1 软化分布、降低置信度（修正过度自信）；T<1 锐化。它**保持 argmax
不变**，因此不改胜平负排序（命中率不变），只改概率大小——进而修正 EV/Kelly/信心。
T 由 ``fit_temperature`` 在历史数据上最小化多分类对数损失拟合得到（凸问题，三分搜索）。

纯数学、无外部依赖，便于单测。
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from app.models.value_objects.probability import Probability
from app.models.value_objects.score import MatchResult

# 概率向量的固定顺序：主 / 平 / 客。
_ORDER: tuple[MatchResult, ...] = (MatchResult.HOME, MatchResult.DRAW, MatchResult.AWAY)
_EPS = 1e-12


def apply_temperature(probs: Sequence[float], temperature: float) -> list[float]:
    """对概率向量做温度缩放并归一化；T=1 原样返回（含归一化）。"""
    if temperature <= 0:
        raise ValueError("temperature 必须为正数")
    inv = 1.0 / temperature
    powered = [max(p, _EPS) ** inv for p in probs]
    total = sum(powered)
    if total <= 0:  # 理论不可达（已做 eps 保护），保险起见退回均匀分布
        n = len(probs)
        return [1.0 / n] * n
    return [p / total for p in powered]


def log_loss(samples: Sequence[tuple[Sequence[float], int]], temperature: float = 1.0) -> float:
    """多分类对数损失：对每个样本按 temperature 缩放后取 -ln p_actual 的均值。"""
    if not samples:
        return 0.0
    total = 0.0
    for probs, actual in samples:
        scaled = apply_temperature(probs, temperature)
        total += -math.log(max(scaled[actual], _EPS))
    return total / len(samples)


def fit_temperature(
    samples: Sequence[tuple[Sequence[float], int]],
    *,
    lo: float = 0.25,
    hi: float = 5.0,
    iterations: int = 60,
) -> float:
    """在 [lo, hi] 上用三分搜索最小化多分类对数损失，返回最优 T。

    ``samples`` 为 (概率向量, 实际类别下标) 列表；下标须与向量同序（0=主,1=平,2=客）。
    对数损失关于 1/T 凸，三分搜索稳健收敛。样本为空返回 1.0（恒等）。
    """
    if not samples:
        return 1.0
    a, b = lo, hi
    for _ in range(iterations):
        m1 = a + (b - a) / 3.0
        m2 = b - (b - a) / 3.0
        if log_loss(samples, m1) < log_loss(samples, m2):
            b = m2
        else:
            a = m1
    return (a + b) / 2.0


class TemperatureCalibrator:
    """把温度缩放应用到 1X2 概率字典上的校准器（T=1 时为恒等，零开销）。"""

    def __init__(self, temperature: float = 1.0) -> None:
        if temperature <= 0:
            raise ValueError("temperature 必须为正数")
        self._t = temperature

    @property
    def temperature(self) -> float:
        return self._t

    def calibrate(self, probs: dict[MatchResult, Probability]) -> dict[MatchResult, Probability]:
        if self._t == 1.0:
            return probs
        scaled = apply_temperature([probs[r].value for r in _ORDER], self._t)
        return {r: Probability(v) for r, v in zip(_ORDER, scaled, strict=True)}
