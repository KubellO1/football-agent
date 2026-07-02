"""决策相关值对象。

把系统宪法（docs/agent-constitution.md）中的硬阈值概念编码为类型安全、
不可变、自校验的值对象，供 service 层的准入 gate 使用。这里只承载概念与
约束，不包含任何足球算法或数值预测逻辑。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import ClassVar

# 宪法默认阈值（第 5、8 节）。集中在此便于日后调整或迁移到配置。
DEFAULT_MIN_EVIDENCE_LEVEL_RANK = 4  # 对应 B 级
DEFAULT_MIN_DATA_COMPLETENESS = 90.0  # 百分比
DEFAULT_MIN_DECISION_SCORE = 85.0  # 综合评分


class EvidenceLevel(str, Enum):
    """证据等级（宪法第 5 节）。A 最高，E 最低。"""

    A = "A"  # 官方
    B = "B"  # 专业统计
    C = "C"  # 权威媒体
    D = "D"  # 社区
    E = "E"  # 推测

    @property
    def rank(self) -> int:
        """数值化等级，便于比较；A=5 ... E=1。"""
        return {"A": 5, "B": 4, "C": 3, "D": 2, "E": 1}[self.value]

    def meets_minimum(self, minimum: EvidenceLevel) -> bool:
        """是否达到（不低于）给定的最低证据等级。"""
        return self.rank >= minimum.rank


class RiskLevel(str, Enum):
    """风险等级（宪法第 10 节）。"""

    LOW = "low"  # 低
    MEDIUM = "medium"  # 中
    HIGH = "high"  # 高


@dataclass(frozen=True, slots=True)
class DataCompleteness:
    """数据完整度，取值 0-100（宪法第 4.3 节）。"""

    value: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.value <= 100.0:
            raise ValueError("数据完整度必须在 0-100 之间")

    def is_sufficient(self, threshold: float = DEFAULT_MIN_DATA_COMPLETENESS) -> bool:
        """是否达到推荐所需的最低完整度（默认 ≥90%）。"""
        return self.value >= threshold


@dataclass(frozen=True, slots=True)
class DecisionScore:
    """综合评分，取值 0-100（宪法第 8 节准入门槛）。"""

    value: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.value <= 100.0:
            raise ValueError("综合评分必须在 0-100 之间")

    def is_recommendable(self, threshold: float = DEFAULT_MIN_DECISION_SCORE) -> bool:
        """是否达到推荐所需的最低评分（默认 ≥85）。"""
        return self.value >= threshold


@dataclass(frozen=True, slots=True)
class StakeUnit:
    """下注单位（宪法第 10 节）。仅允许离散取值，禁止重仓越界。"""

    value: float

    #  0=不下注，1=满仓（一个标准单位）；禁止超过 1。
    ALLOWED: ClassVar[frozenset[float]] = frozenset({0.0, 0.25, 0.5, 0.75, 1.0})

    def __post_init__(self) -> None:
        if self.value not in self.ALLOWED:
            allowed = ", ".join(str(v) for v in sorted(self.ALLOWED))
            raise ValueError(f"下注单位只能取 {allowed}，收到 {self.value}")

    @property
    def is_no_bet(self) -> bool:
        return self.value == 0.0
