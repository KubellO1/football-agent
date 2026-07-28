"""原始统计数据的质量评估值对象。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from app.models.value_objects.decision import DataCompleteness, EvidenceLevel

if TYPE_CHECKING:
    from datetime import datetime

    from app.models.value_objects.statistics import StatisticField


class DataFreshness(StrEnum):
    """数据相对于评估时点的新鲜度。"""

    FRESH = "fresh"
    STALE = "stale"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class DataQualityAssessment:
    """一次可复现的数据质量评估，不负责计算统计或预测结果。"""

    completeness: DataCompleteness
    evidence_level: EvidenceLevel
    freshness: DataFreshness
    evaluated_at: datetime
    missing_fields: frozenset[StatisticField] = field(default_factory=frozenset)
    conflicting_fields: frozenset[StatisticField] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if self.evaluated_at.tzinfo is None or self.evaluated_at.utcoffset() is None:
            raise ValueError("evaluated_at must be timezone-aware")
        overlap = self.missing_fields & self.conflicting_fields
        if overlap:
            names = ", ".join(sorted(item.value for item in overlap))
            raise ValueError(f"fields cannot be both missing and conflicting: {names}")

    def is_usable(
        self,
        *,
        minimum_completeness: float = 90.0,
        minimum_evidence: EvidenceLevel = EvidenceLevel.B,
    ) -> bool:
        """只有完整、可信、新鲜且无冲突的数据才能进入推荐模型。"""
        return (
            self.completeness.is_sufficient(minimum_completeness)
            and self.evidence_level.meets_minimum(minimum_evidence)
            and self.freshness is DataFreshness.FRESH
            and not self.conflicting_fields
        )
