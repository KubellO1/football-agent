"""Aggregated statistics value objects.

Immutable snapshots of form/output for a team or player over some window.
The window itself (season, last-N) is defined by whatever attaches these.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite


@dataclass(frozen=True, slots=True)
class TeamStatistics:
    """Aggregated team performance over a window of matches."""

    matches_played: int
    wins: int
    draws: int
    losses: int
    goals_for: int
    goals_against: int
    xg_for: float = 0.0
    xg_against: float = 0.0

    def __post_init__(self) -> None:
        counts = [
            self.matches_played,
            self.wins,
            self.draws,
            self.losses,
            self.goals_for,
            self.goals_against,
        ]
        if any(c < 0 for c in counts):
            raise ValueError("statistical counts cannot be negative")
        if self.wins + self.draws + self.losses > self.matches_played:
            raise ValueError("results exceed matches played")

    @property
    def points(self) -> int:
        return self.wins * 3 + self.draws

    @property
    def goal_difference(self) -> int:
        return self.goals_for - self.goals_against


@dataclass(frozen=True, slots=True)
class PlayerStatistics:
    """Aggregated player output over a window of matches."""

    appearances: int
    minutes: int
    goals: int
    assists: int
    xg: float = 0.0
    xa: float = 0.0
    yellow_cards: int = 0
    red_cards: int = 0

    def __post_init__(self) -> None:
        values = [
            self.appearances,
            self.minutes,
            self.goals,
            self.assists,
            self.yellow_cards,
            self.red_cards,
        ]
        if any(v < 0 for v in values):
            raise ValueError("statistical values cannot be negative")


class StatisticField(StrEnum):
    """球队单场统计字段，供完整度和冲突报告使用。"""

    XG = "xg"
    XG_AGAINST = "xg_against"
    SHOTS = "shots"
    SHOTS_ON_TARGET = "shots_on_target"
    POSSESSION_PERCENTAGE = "possession_percentage"
    PPDA = "ppda"
    BIG_CHANCES = "big_chances"
    GOALKEEPER_SAVES = "goalkeeper_saves"
    SET_PIECE_SHOTS = "set_piece_shots"
    HEADED_SHOTS = "headed_shots"
    CONVERSION_RATE = "conversion_rate"


@dataclass(frozen=True, slots=True)
class TeamMatchMetrics:
    """单支球队在一场比赛中的原始指标；未知值必须保持为 ``None``。"""

    xg: float | None = None
    xg_against: float | None = None
    shots: int | None = None
    shots_on_target: int | None = None
    possession_percentage: float | None = None
    ppda: float | None = None
    big_chances: int | None = None
    goalkeeper_saves: int | None = None
    set_piece_shots: int | None = None
    headed_shots: int | None = None
    conversion_rate: float | None = None

    def __post_init__(self) -> None:
        counts = {
            "shots": self.shots,
            "shots_on_target": self.shots_on_target,
            "big_chances": self.big_chances,
            "goalkeeper_saves": self.goalkeeper_saves,
            "set_piece_shots": self.set_piece_shots,
            "headed_shots": self.headed_shots,
        }
        for name, value in counts.items():
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < 0
            ):
                raise ValueError(f"{name} must be a non-negative integer or None")

        self._validate_non_negative("xg", self.xg)
        self._validate_non_negative("xg_against", self.xg_against)
        self._validate_range("possession_percentage", self.possession_percentage, 0.0, 100.0)
        self._validate_positive("ppda", self.ppda)
        self._validate_range("conversion_rate", self.conversion_rate, 0.0, 1.0)

        if (
            self.shots is not None
            and self.shots_on_target is not None
            and self.shots_on_target > self.shots
        ):
            raise ValueError("shots_on_target cannot exceed shots")
        if (
            self.shots is not None
            and self.set_piece_shots is not None
            and self.set_piece_shots > self.shots
        ):
            raise ValueError("set_piece_shots cannot exceed shots")
        if (
            self.shots is not None
            and self.headed_shots is not None
            and self.headed_shots > self.shots
        ):
            raise ValueError("headed_shots cannot exceed shots")

    @property
    def available_fields(self) -> frozenset[StatisticField]:
        """返回真实存在的字段；值为 0 仍属于已观测数据。"""
        values = {
            StatisticField.XG: self.xg,
            StatisticField.XG_AGAINST: self.xg_against,
            StatisticField.SHOTS: self.shots,
            StatisticField.SHOTS_ON_TARGET: self.shots_on_target,
            StatisticField.POSSESSION_PERCENTAGE: self.possession_percentage,
            StatisticField.PPDA: self.ppda,
            StatisticField.BIG_CHANCES: self.big_chances,
            StatisticField.GOALKEEPER_SAVES: self.goalkeeper_saves,
            StatisticField.SET_PIECE_SHOTS: self.set_piece_shots,
            StatisticField.HEADED_SHOTS: self.headed_shots,
            StatisticField.CONVERSION_RATE: self.conversion_rate,
        }
        return frozenset(field for field, value in values.items() if value is not None)

    @property
    def missing_fields(self) -> frozenset[StatisticField]:
        """返回缺失字段；不把未知值转换为零。"""
        return frozenset(StatisticField) - self.available_fields

    @staticmethod
    def _validate_non_negative(name: str, value: float | None) -> None:
        if value is not None and (not isfinite(value) or value < 0.0):
            raise ValueError(f"{name} must be finite and non-negative or None")

    @staticmethod
    def _validate_positive(name: str, value: float | None) -> None:
        if value is not None and (not isfinite(value) or value <= 0.0):
            raise ValueError(f"{name} must be finite and positive or None")

    @staticmethod
    def _validate_range(name: str, value: float | None, minimum: float, maximum: float) -> None:
        if value is not None and (not isfinite(value) or value < minimum or value > maximum):
            raise ValueError(f"{name} must be between {minimum} and {maximum} or None")
