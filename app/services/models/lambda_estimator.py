"""强度 → λ 估计。

把球队的进攻/防守表现转换为 Poisson 模型所需的预期进球 λ。采用经典的
独立-泊松强度法：以联赛场均进球为基准，算出每队相对强度，再结合主场优势
得到主客队各自的 λ。是「数据 → λ → 概率」链路中承上启下的一环。

    attack_i  = 场均进球_i / 联赛场均进球
    defense_i = 场均失球_i / 联赛场均进球
    λ_home = attack_home × defense_away × 联赛场均 × 主场优势
    λ_away = attack_away × defense_home × 联赛场均
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.value_objects.statistics import TeamStatistics


@dataclass(frozen=True, slots=True)
class LeagueAverages:
    """联赛基准：每队场均进球（整体口径）。"""

    goals_per_game: float

    def __post_init__(self) -> None:
        if self.goals_per_game <= 0:
            raise ValueError("联赛场均进球必须为正数")


class LambdaEstimator:
    """由球队统计估计主客队 λ。"""

    def __init__(self, *, home_advantage: float = 1.15, use_xg: bool = True) -> None:
        if home_advantage <= 0:
            raise ValueError("主场优势系数必须为正数")
        self._home_advantage = home_advantage
        self._use_xg = use_xg

    def _scored_per_game(self, stats: TeamStatistics) -> float:
        total = stats.xg_for if self._use_xg else float(stats.goals_for)
        return total / stats.matches_played

    def _conceded_per_game(self, stats: TeamStatistics) -> float:
        total = stats.xg_against if self._use_xg else float(stats.goals_against)
        return total / stats.matches_played

    def estimate(
        self,
        home_stats: TeamStatistics,
        away_stats: TeamStatistics,
        league: LeagueAverages,
    ) -> tuple[float, float]:
        """返回 (λ_home, λ_away)。"""
        if home_stats.matches_played <= 0 or away_stats.matches_played <= 0:
            raise ValueError("球队场次为 0，数据不足以估计 λ")

        avg = league.goals_per_game
        home_attack = self._scored_per_game(home_stats) / avg
        home_defense = self._conceded_per_game(home_stats) / avg
        away_attack = self._scored_per_game(away_stats) / avg
        away_defense = self._conceded_per_game(away_stats) / avg

        lam_home = home_attack * away_defense * avg * self._home_advantage
        lam_away = away_attack * home_defense * avg
        return lam_home, lam_away
