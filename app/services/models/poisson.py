"""Poisson 比分模型。

由主客预期进球 λ 生成比分分布矩阵，并推导各投注市场的概率：胜平负(1X2)、
大小球(Over/Under)、双方进球(BTTS)、正确比分。纯数学、无外部依赖。

单队进 k 球：P(k; λ) = λ^k · e^(−λ) / k!
比分联合概率（进球数相互独立）：P(i, j) = P(i; λ_home) · P(j; λ_away)
截断到 max_goals 后做归一化，保证概率和为 1。
"""

from __future__ import annotations

import math

from app.models.value_objects.probability import Probability
from app.models.value_objects.score import MatchResult, Score


def _clamp(value: float) -> float:
    """把浮点数夹到 [0, 1]，抵消累加/截断带来的极小误差。"""
    return min(1.0, max(0.0, value))


class PoissonModel:
    """基于泊松分布的比分/市场概率模型。"""

    def __init__(self, max_goals: int = 10) -> None:
        if max_goals < 1:
            raise ValueError("max_goals 必须为正整数")
        self._max = max_goals

    @staticmethod
    def _pmf(k: int, lam: float) -> float:
        return lam**k * math.exp(-lam) / math.factorial(k)

    def score_matrix(self, lam_home: float, lam_away: float) -> list[list[float]]:
        """归一化后的比分矩阵，matrix[i][j] = P(主队 i 球, 客队 j 球)。"""
        if lam_home <= 0 or lam_away <= 0:
            raise ValueError("预期进球 λ 必须为正数")
        home = [self._pmf(i, lam_home) for i in range(self._max + 1)]
        away = [self._pmf(j, lam_away) for j in range(self._max + 1)]
        matrix = [[h * a for a in away] for h in home]
        total = sum(sum(row) for row in matrix)
        return [[value / total for value in row] for row in matrix]

    def match_result_probabilities(
        self, lam_home: float, lam_away: float
    ) -> dict[MatchResult, Probability]:
        """胜平负(1X2)概率。"""
        matrix = self.score_matrix(lam_home, lam_away)
        home = draw = away = 0.0
        for i, row in enumerate(matrix):
            for j, prob in enumerate(row):
                if i > j:
                    home += prob
                elif i == j:
                    draw += prob
                else:
                    away += prob
        return {
            MatchResult.HOME: Probability(_clamp(home)),
            MatchResult.DRAW: Probability(_clamp(draw)),
            MatchResult.AWAY: Probability(_clamp(away)),
        }

    def over_under(
        self, lam_home: float, lam_away: float, line: float
    ) -> tuple[Probability, Probability]:
        """大小球概率，返回 (over, under)。line 通常为半整数（如 2.5）。"""
        matrix = self.score_matrix(lam_home, lam_away)
        over = sum(
            prob
            for i, row in enumerate(matrix)
            for j, prob in enumerate(row)
            if i + j > line
        )
        over = _clamp(over)
        return Probability(over), Probability(_clamp(1.0 - over))

    def both_teams_to_score(
        self, lam_home: float, lam_away: float
    ) -> tuple[Probability, Probability]:
        """双方进球概率，返回 (yes, no)。"""
        matrix = self.score_matrix(lam_home, lam_away)
        yes = sum(
            prob
            for i, row in enumerate(matrix)
            for j, prob in enumerate(row)
            if i >= 1 and j >= 1
        )
        yes = _clamp(yes)
        return Probability(yes), Probability(_clamp(1.0 - yes))

    def top_correct_scores(
        self, lam_home: float, lam_away: float, top: int = 3
    ) -> list[tuple[Score, Probability]]:
        """概率最高的前 N 个正确比分。"""
        matrix = self.score_matrix(lam_home, lam_away)
        scores = [
            (Score(home=i, away=j), prob)
            for i, row in enumerate(matrix)
            for j, prob in enumerate(row)
        ]
        scores.sort(key=lambda item: item[1], reverse=True)
        return [(score, Probability(_clamp(prob))) for score, prob in scores[:top]]
