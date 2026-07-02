"""Elo 评分模型。

提供含主场优势的期望得分，以及按赛果更新评分的方法（零和守恒），可作为独立
的实力评分来源，并支撑长期学习中的评分/权重更新。纯数学、无外部依赖。

    期望得分：E_home = 1 / (1 + 10^((R_away − (R_home + home_adv)) / 400))
    评分更新：R' = R ± K·(S − E)，S 为实际得分（胜1/平0.5/负0）
"""

from __future__ import annotations

from app.models.value_objects.score import MatchResult

_RESULT_SCORE: dict[MatchResult, float] = {
    MatchResult.HOME: 1.0,
    MatchResult.DRAW: 0.5,
    MatchResult.AWAY: 0.0,
}


class EloModel:
    """Elo 评分计算与更新。"""

    def __init__(self, *, k_factor: float = 20.0, home_advantage: float = 65.0) -> None:
        if k_factor <= 0:
            raise ValueError("k_factor 必须为正数")
        self._k = k_factor
        self._home_advantage = home_advantage

    def expected_score(self, home_rating: float, away_rating: float) -> float:
        """主队期望得分（0-1），已计入主场优势。"""
        diff = away_rating - (home_rating + self._home_advantage)
        return 1.0 / (1.0 + 10 ** (diff / 400.0))

    @staticmethod
    def result_to_score(result: MatchResult) -> float:
        """把赛果映射为主队实际得分：胜1/平0.5/负0。"""
        return _RESULT_SCORE[result]

    def update(
        self, home_rating: float, away_rating: float, result: MatchResult
    ) -> tuple[float, float]:
        """按赛果更新评分，返回 (新主队评分, 新客队评分)。零和守恒。"""
        actual = self.result_to_score(result)
        expected = self.expected_score(home_rating, away_rating)
        delta = self._k * (actual - expected)
        return home_rating + delta, away_rating - delta
