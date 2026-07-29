"""强度 → λ 估计。

把球队的进攻/防守表现转换为 Poisson 模型所需的预期进球 λ。采用经典的
独立-泊松强度法：以联赛场均进球为基准，算出每队相对强度，再结合主场优势
得到主客队各自的 λ。是「数据 → λ → 概率」链路中承上启下的一环。

    attack_i  = 场均进球_i / 联赛场均进球
    defense_i = 场均失球_i / 联赛场均进球
    λ_home = attack_home × defense_away × 联赛场均 × 主场优势
    λ_away = attack_away × defense_home × 联赛场均

2026-07-11: 添加 λ 下限保护 (0.05)，防止零进球/无历史球队导致 Poisson 崩溃。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.value_objects.statistics import TeamStatistics

logger = logging.getLogger(__name__)

# λ 安全下限：防止零/负 λ 导致 PoissonModel.score_matrix() 抛出 ValueError。
# 0.05 代表每 20 场比赛预期进 1 球——极保守，但保证数值稳定。
LAMBDA_FLOOR: float = 0.05


class LambdaWarningType(StrEnum):
    GENUINE_LOW = "genuine_low"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass(frozen=True, slots=True)
class LambdaWarning:
    team: str
    raw_lambda: float
    warning_type: LambdaWarningType
    reason: str


@dataclass(frozen=True, slots=True)
class LambdaEstimate:
    """λ 估计结果，含警告列表（供下游降级决策）。"""

    lam_home: float
    lam_away: float
    warnings: list[LambdaWarning] = field(default_factory=list)

    @property
    def has_insufficient_data(self) -> bool:
        return any(w.warning_type == LambdaWarningType.INSUFFICIENT_DATA for w in self.warnings)


class BaselineMetric(StrEnum):
    """联赛强度基准使用的统计口径。"""

    GOALS = "goals"
    XG = "xg"


@dataclass(frozen=True, slots=True)
class LeagueBaseline:
    """指标显式的每队每场联赛强度基准。"""

    rate_per_team_match: float
    metric: BaselineMetric

    def __post_init__(self) -> None:
        if (
            isinstance(self.rate_per_team_match, bool)
            or not isfinite(self.rate_per_team_match)
            or self.rate_per_team_match <= 0.0
        ):
            raise ValueError("league baseline rate must be finite and positive")
        if not isinstance(self.metric, BaselineMetric):
            raise ValueError("league baseline metric must be a BaselineMetric member")


@dataclass(frozen=True, slots=True)
class LeagueAverages:
    """联赛基准：每队场均进球（整体口径）。"""

    goals_per_game: float

    def __post_init__(self) -> None:
        if isinstance(self.goals_per_game, bool) or not isfinite(self.goals_per_game):
            raise ValueError("league goals per game must be finite and positive")
        if self.goals_per_game <= 0:
            raise ValueError("联赛场均进球必须为正数")

    @property
    def metric(self) -> BaselineMetric:
        return BaselineMetric.GOALS

    @property
    def rate_per_team_match(self) -> float:
        return self.goals_per_game

    def to_baseline(self) -> LeagueBaseline:
        """显式转换为实际进球口径的新契约。"""
        return LeagueBaseline(
            rate_per_team_match=self.goals_per_game,
            metric=BaselineMetric.GOALS,
        )


class LambdaEstimator:
    """由球队统计估计主客队 λ。"""

    def __init__(
        self,
        *,
        home_advantage: float = 1.15,
        lambda_floor: float = LAMBDA_FLOOR,
    ) -> None:
        if home_advantage <= 0:
            raise ValueError("主场优势系数必须为正数")
        if lambda_floor <= 0:
            raise ValueError("λ 下限必须为正数")
        self._home_advantage = home_advantage
        self._floor = lambda_floor

    @staticmethod
    def _scored_total(stats: TeamStatistics, metric: BaselineMetric) -> float:
        return stats.xg_for if metric is BaselineMetric.XG else float(stats.goals_for)

    @staticmethod
    def _conceded_total(stats: TeamStatistics, metric: BaselineMetric) -> float:
        return stats.xg_against if metric is BaselineMetric.XG else float(stats.goals_against)

    @classmethod
    def _scored_per_game(cls, stats: TeamStatistics, metric: BaselineMetric) -> float:
        return cls._scored_total(stats, metric) / stats.matches_played

    @classmethod
    def _conceded_per_game(cls, stats: TeamStatistics, metric: BaselineMetric) -> float:
        return cls._conceded_total(stats, metric) / stats.matches_played

    def _apply_floor(
        self,
        raw: float,
        team_name: str,
        source_total: float,
        matches: int,
        metric: BaselineMetric,
    ) -> tuple[float, LambdaWarning | None]:
        """对单个 λ 值应用下限保护，返回 (安全值, 警告或 None)。"""
        if raw > self._floor:
            return raw, None

        if raw <= 0:
            source_name = "xg_for" if metric is BaselineMetric.XG else "goals_for"
            warning = LambdaWarning(
                team=team_name,
                raw_lambda=raw,
                warning_type=LambdaWarningType.INSUFFICIENT_DATA,
                reason=(
                    f"insufficient scoring history: {source_name}={source_total:g}, "
                    f"matches={matches}, raw λ={raw:.4f} ≤ 0"
                ),
            )
            logger.warning(
                "Lambda floor applied [INSUFFICIENT_DATA] team=%s raw_λ=%.4f floor=%.4f "
                "metric=%s source_total=%.4f matches=%d",
                team_name,
                raw,
                self._floor,
                metric.value,
                source_total,
                matches,
            )
        else:
            warning = LambdaWarning(
                team=team_name,
                raw_lambda=raw,
                warning_type=LambdaWarningType.GENUINE_LOW,
                reason=(f"genuine low-scoring estimate: raw λ={raw:.4f} below floor={self._floor}"),
            )
            logger.info(
                "Lambda floor applied [GENUINE_LOW] team=%s raw_λ=%.4f floor=%.4f",
                team_name,
                raw,
                self._floor,
            )

        return self._floor, warning

    def estimate(
        self,
        home_stats: TeamStatistics,
        away_stats: TeamStatistics,
        league: LeagueBaseline | LeagueAverages,
    ) -> LambdaEstimate:
        """返回 LambdaEstimate（含 λ 值与保护警告）；任一 λ 低于 floor 时自动应用保护。

        当任一球队 matches_played <= 0 时，直接返回双方均为 floor 值 + INSUFFICIENT_DATA
        警告，不再抛出异常——保证生产管道不会因缺失历史数据而崩溃。
        """
        warnings: list[LambdaWarning] = []

        if home_stats.matches_played <= 0 or away_stats.matches_played <= 0:
            # 无法估计：双方统一退回下限，标记 INSUFFICIENT_DATA
            zero_home_warn = LambdaWarning(
                team="home",
                raw_lambda=0.0,
                warning_type=LambdaWarningType.INSUFFICIENT_DATA,
                reason=(
                    f"matches_played={home_stats.matches_played} (home) / "
                    f"{away_stats.matches_played} (away): cannot estimate λ"
                ),
            )
            warnings.append(zero_home_warn)
            logger.warning(
                "Lambda floor applied [INSUFFICIENT_DATA] matches_played <= 0 "
                "(home=%d, away=%d), forcing both λ to floor=%.4f",
                home_stats.matches_played,
                away_stats.matches_played,
                self._floor,
            )
            return LambdaEstimate(
                lam_home=self._floor,
                lam_away=self._floor,
                warnings=warnings,
            )

        baseline = league.to_baseline() if isinstance(league, LeagueAverages) else league
        avg = baseline.rate_per_team_match
        metric = baseline.metric
        home_attack = self._scored_per_game(home_stats, metric) / avg
        home_defense = self._conceded_per_game(home_stats, metric) / avg
        away_attack = self._scored_per_game(away_stats, metric) / avg
        away_defense = self._conceded_per_game(away_stats, metric) / avg

        raw_home = home_attack * away_defense * avg * self._home_advantage
        raw_away = away_attack * home_defense * avg

        safe_home, warn_home = self._apply_floor(
            raw_home,
            "home",
            self._scored_total(home_stats, metric),
            home_stats.matches_played,
            metric,
        )
        safe_away, warn_away = self._apply_floor(
            raw_away,
            "away",
            self._scored_total(away_stats, metric),
            away_stats.matches_played,
            metric,
        )

        if warn_home is not None:
            warnings.append(warn_home)
        if warn_away is not None:
            warnings.append(warn_away)

        return LambdaEstimate(lam_home=safe_home, lam_away=safe_away, warnings=warnings)
