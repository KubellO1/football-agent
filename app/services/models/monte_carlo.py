"""蒙特卡洛比赛模拟。

给定主客队预期进球 λ，重复模拟大量比赛（每场用 Knuth 算法从 Poisson(λ) 采样
进球数），以频率估计胜平负等市场概率。用于交叉校验 Poisson 解析解，并为难以
解析求解的复杂市场提供概率估计。

用固定随机种子可复现结果，便于测试与审计。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from app.models.value_objects.probability import Probability
from app.models.value_objects.score import MatchResult


@dataclass(frozen=True, slots=True)
class SimulationResult:
    """一次蒙特卡洛模拟的汇总。"""

    outcome_probabilities: dict[MatchResult, Probability]
    iterations: int


class MonteCarloModel:
    """基于随机模拟的比赛概率估计。"""

    def __init__(self, *, iterations: int = 10000, seed: int | None = None) -> None:
        if iterations <= 0:
            raise ValueError("模拟次数必须为正整数")
        self._iterations = iterations
        self._rng = random.Random(seed)

    def _sample_poisson(self, lam: float) -> int:
        """Knuth 算法采样单个 Poisson(λ) 变量。"""
        threshold = math.exp(-lam)
        k = 0
        product = 1.0
        while True:
            k += 1
            product *= self._rng.random()
            if product <= threshold:
                return k - 1

    def simulate(self, lam_home: float, lam_away: float) -> SimulationResult:
        """模拟并返回胜平负概率。"""
        if lam_home <= 0 or lam_away <= 0:
            raise ValueError("预期进球 λ 必须为正数")

        home = draw = away = 0
        for _ in range(self._iterations):
            goals_home = self._sample_poisson(lam_home)
            goals_away = self._sample_poisson(lam_away)
            if goals_home > goals_away:
                home += 1
            elif goals_home == goals_away:
                draw += 1
            else:
                away += 1

        n = self._iterations
        return SimulationResult(
            outcome_probabilities={
                MatchResult.HOME: Probability(home / n),
                MatchResult.DRAW: Probability(draw / n),
                MatchResult.AWAY: Probability(away / n),
            },
            iterations=n,
        )

    def match_result_probabilities(
        self, lam_home: float, lam_away: float
    ) -> dict[MatchResult, Probability]:
        """与 PoissonModel 同名接口，便于对比/替换。"""
        return self.simulate(lam_home, lam_away).outcome_probabilities
