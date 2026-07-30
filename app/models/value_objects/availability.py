"""球员可用性领域值对象。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.value_objects.decision import EvidenceLevel


class AvailabilityStatus(StrEnum):
    """数据源在特定时点报告的球员可用性状态。"""

    UNKNOWN = "unknown"
    AVAILABLE = "available"
    DOUBTFUL = "doubtful"
    OUT = "out"
    SUSPENDED = "suspended"
    RETURNED = "returned"

    @property
    def is_known(self) -> bool:
        """UNKNOWN 表示缺少结论，不能按可出场处理。"""
        return self is not AvailabilityStatus.UNKNOWN

    @property
    def rules_player_out(self) -> bool:
        """只有明确缺阵或停赛才能直接判定球员无法出场。"""
        return self in (AvailabilityStatus.OUT, AvailabilityStatus.SUSPENDED)


@dataclass(frozen=True, slots=True)
class AvailabilitySource:
    """一条可用性观察的来源身份与证据等级。"""

    name: str
    evidence_level: EvidenceLevel
    reference: str | None = None

    def __post_init__(self) -> None:
        name = self.name.strip()
        if not name:
            raise ValueError("source name cannot be empty")
        if len(name) > 80:
            raise ValueError("source name cannot exceed 80 characters")
        object.__setattr__(self, "name", name)

        if self.reference is not None:
            reference = self.reference.strip()
            if not reference:
                raise ValueError("source reference cannot be empty")
            if len(reference) > 500:
                raise ValueError("source reference cannot exceed 500 characters")
            object.__setattr__(self, "reference", reference)
