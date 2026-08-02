"""确定性赛前状态解析器。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.models.value_objects.pre_kickoff import PreKickoffSnapshot, PreKickoffState

if TYPE_CHECKING:
    from app.config.settings import Settings


@dataclass(frozen=True, slots=True)
class PreKickoffThresholds:
    """状态解析所需的生产阈值快照。"""

    min_expected_value: float
    min_confidence: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.min_expected_value):
            raise ValueError("min_expected_value 必须是有限数值")
        if not math.isfinite(self.min_confidence) or not 0.0 <= self.min_confidence <= 1.0:
            raise ValueError("min_confidence 必须在 [0, 1] 范围内")

    @classmethod
    def from_settings(cls, settings: Settings) -> PreKickoffThresholds:
        """从现有生产配置读取阈值，不在解析器中复制默认值。"""
        return cls(
            min_expected_value=settings.recommendations_min_ev,
            min_confidence=settings.recommendations_min_confidence,
        )


class PreKickoffStateResolver:
    """仅根据不可变快照解析状态，无 I/O、无持久化副作用。"""

    def __init__(self, thresholds: PreKickoffThresholds) -> None:
        self._thresholds = thresholds

    @classmethod
    def from_settings(cls, settings: Settings) -> PreKickoffStateResolver:
        """使用现有生产配置创建解析器。"""
        return cls(PreKickoffThresholds.from_settings(settings))

    def resolve(self, snapshot: PreKickoffSnapshot) -> PreKickoffState:
        """按照固定优先级解析当前状态；相同输入始终得到相同输出。"""
        if not snapshot.historical_data_sufficient:
            return PreKickoffState.INSUFFICIENT_HISTORY
        if not snapshot.lineup_available:
            return PreKickoffState.WAITING_FOR_LINEUP
        if not snapshot.odds_available:
            return PreKickoffState.ODDS_MISSING

        expected_value = snapshot.expected_value
        confidence = snapshot.confidence
        if not snapshot.model_probability_available or expected_value is None or confidence is None:
            return PreKickoffState.WATCH

        production_rules_pass = (
            expected_value >= self._thresholds.min_expected_value
            and confidence >= self._thresholds.min_confidence
            and snapshot.gate_passed
            and snapshot.risk_passed
        )
        if production_rules_pass:
            return PreKickoffState.BET
        if snapshot.final_checkpoint_reached:
            return PreKickoffState.FINAL_NO_BET
        return PreKickoffState.WATCH
