"""赛前重评工作流的不可变值对象。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class PreKickoffCheckpoint(StrEnum):
    """赛前自动重评检查点。"""

    T90 = "T90"
    T60 = "T60"
    T30 = "T30"
    POST_T30 = "POST_T30"


class PreKickoffState(StrEnum):
    """赛前重评的显式业务状态。"""

    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    WAITING_FOR_LINEUP = "WAITING_FOR_LINEUP"
    ODDS_MISSING = "ODDS_MISSING"
    WATCH = "WATCH"
    FINAL_NO_BET = "FINAL_NO_BET"
    BET = "BET"


@dataclass(frozen=True, slots=True)
class PreKickoffSnapshot:
    """一次重评所需的完整输入快照，不持有可变依赖。"""

    checkpoint: PreKickoffCheckpoint
    historical_data_sufficient: bool
    lineup_available: bool
    odds_available: bool
    model_probability_available: bool
    expected_value: float | None
    confidence: float | None
    gate_passed: bool
    risk_passed: bool
    kickoff_time: datetime
    current_time: datetime
    previous_state: PreKickoffState | None = None

    def __post_init__(self) -> None:
        if self.kickoff_time.tzinfo is None or self.current_time.tzinfo is None:
            raise ValueError("kickoff_time 和 current_time 必须包含时区")
        if self.expected_value is not None and not math.isfinite(self.expected_value):
            raise ValueError("expected_value 必须是有限数值或 None")
        if self.confidence is not None and (
            not math.isfinite(self.confidence) or not 0.0 <= self.confidence <= 1.0
        ):
            raise ValueError("confidence 必须在 [0, 1] 范围内或为 None")

    @property
    def final_checkpoint_reached(self) -> bool:
        """显式检查点或实际时间已经到达 T-30。"""
        return self.checkpoint in {
            PreKickoffCheckpoint.T30,
            PreKickoffCheckpoint.POST_T30,
        } or self.current_time >= self.kickoff_time - timedelta(minutes=30)
