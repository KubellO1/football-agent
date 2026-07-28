"""球队单场统计的确定性数据质量评估。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from math import isfinite
from types import MappingProxyType
from typing import TYPE_CHECKING

from app.models.value_objects.data_quality import DataFreshness, DataQualityAssessment
from app.models.value_objects.decision import DataCompleteness, EvidenceLevel
from app.models.value_objects.statistics import StatisticField

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from app.models.entities.team_match_statistics import TeamMatchStatistics

_DEFAULT_FIELD_WEIGHTS: dict[StatisticField, float] = {
    StatisticField.XG: 15.0,
    StatisticField.XG_AGAINST: 15.0,
    StatisticField.SHOTS: 10.0,
    StatisticField.SHOTS_ON_TARGET: 10.0,
    StatisticField.POSSESSION_PERCENTAGE: 8.0,
    StatisticField.PPDA: 10.0,
    StatisticField.BIG_CHANCES: 8.0,
    StatisticField.GOALKEEPER_SAVES: 8.0,
    StatisticField.SET_PIECE_SHOTS: 5.0,
    StatisticField.HEADED_SHOTS: 5.0,
    StatisticField.CONVERSION_RATE: 6.0,
}

_DEFAULT_FLOAT_TOLERANCES: dict[StatisticField, float] = {
    StatisticField.XG: 0.15,
    StatisticField.XG_AGAINST: 0.15,
    StatisticField.POSSESSION_PERCENTAGE: 2.0,
    StatisticField.PPDA: 1.0,
    StatisticField.CONVERSION_RATE: 0.02,
}

_FLOAT_FIELDS = frozenset(_DEFAULT_FLOAT_TOLERANCES)


@dataclass(frozen=True, slots=True)
class DataQualityPolicy:
    """显式且可审计的数据质量规则。"""

    field_weights: Mapping[StatisticField, float] = field(
        default_factory=lambda: dict(_DEFAULT_FIELD_WEIGHTS)
    )
    source_evidence: Mapping[str, EvidenceLevel] = field(default_factory=dict)
    float_tolerances: Mapping[StatisticField, float] = field(
        default_factory=lambda: dict(_DEFAULT_FLOAT_TOLERANCES)
    )
    require_final: bool = True
    provisional_max_age: timedelta = timedelta(minutes=15)

    def __post_init__(self) -> None:
        weights = dict(self.field_weights)
        if set(weights) != set(StatisticField):
            raise ValueError("field_weights must define every statistic field")
        if any(
            isinstance(value, bool) or not isfinite(value) or value <= 0.0
            for value in weights.values()
        ):
            raise ValueError("field weights must be finite and positive")
        if not isfinite(sum(weights.values())) or abs(sum(weights.values()) - 100.0) > 1e-9:
            raise ValueError("field weights must total 100")

        evidence = dict(self.source_evidence)
        if any(not source or source != source.strip() for source in evidence):
            raise ValueError("source evidence keys must be non-empty and trimmed")
        if any(not isinstance(level, EvidenceLevel) for level in evidence.values()):
            raise ValueError("source evidence values must be EvidenceLevel members")

        tolerances = dict(self.float_tolerances)
        if not set(tolerances).issubset(_FLOAT_FIELDS):
            raise ValueError("tolerances may only be configured for floating-point fields")
        if any(
            isinstance(value, bool) or not isfinite(value) or value < 0.0
            for value in tolerances.values()
        ):
            raise ValueError("float tolerances must be finite and non-negative")
        if self.provisional_max_age <= timedelta(0):
            raise ValueError("provisional_max_age must be positive")

        object.__setattr__(self, "field_weights", MappingProxyType(weights))
        object.__setattr__(self, "source_evidence", MappingProxyType(evidence))
        object.__setattr__(self, "float_tolerances", MappingProxyType(tolerances))


class DataQualityEvaluator:
    """从原始快照计算质量，不补值、不预测、不修改任何输入。"""

    def __init__(self, policy: DataQualityPolicy) -> None:
        self._policy = policy

    def evaluate(
        self,
        primary: TeamMatchStatistics,
        *,
        evaluated_at: datetime,
        corroborating: Sequence[TeamMatchStatistics] = (),
    ) -> DataQualityAssessment:
        """评估主快照，并用其他来源的同场同队快照检测冲突。"""
        if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
            raise ValueError("evaluated_at must be timezone-aware")

        snapshots = (primary, *corroborating)
        self._validate_snapshot_set(snapshots, evaluated_at)

        available = primary.metrics.available_fields
        completeness = sum(self._policy.field_weights[item] for item in available)
        conflicts = self._conflicting_fields(primary, corroborating)
        evidence = self._policy.source_evidence.get(
            primary.source,
            EvidenceLevel.E,
        )

        return DataQualityAssessment(
            completeness=DataCompleteness(completeness),
            evidence_level=evidence,
            freshness=self._freshness(snapshots, evaluated_at),
            evaluated_at=evaluated_at,
            missing_fields=primary.metrics.missing_fields,
            conflicting_fields=conflicts,
        )

    def _validate_snapshot_set(
        self,
        snapshots: Sequence[TeamMatchStatistics],
        evaluated_at: datetime,
    ) -> None:
        primary = snapshots[0]
        sources: set[str] = set()
        for snapshot in snapshots:
            if snapshot.fixture_id != primary.fixture_id or snapshot.team_id != primary.team_id:
                raise ValueError("all snapshots must describe the same fixture and team")
            if snapshot.source in sources:
                raise ValueError("only one snapshot per source may be evaluated")
            if snapshot.captured_at > evaluated_at:
                raise ValueError("snapshot captured_at cannot be later than evaluated_at")
            sources.add(snapshot.source)

    def _freshness(
        self,
        snapshots: Sequence[TeamMatchStatistics],
        evaluated_at: datetime,
    ) -> DataFreshness:
        if self._policy.require_final:
            return (
                DataFreshness.FRESH
                if all(snapshot.is_final for snapshot in snapshots)
                else DataFreshness.UNKNOWN
            )

        oldest_capture = min(snapshot.captured_at for snapshot in snapshots)
        if evaluated_at - oldest_capture <= self._policy.provisional_max_age:
            return DataFreshness.FRESH
        return DataFreshness.STALE

    def _conflicting_fields(
        self,
        primary: TeamMatchStatistics,
        corroborating: Sequence[TeamMatchStatistics],
    ) -> frozenset[StatisticField]:
        conflicts: set[StatisticField] = set()
        for other in corroborating:
            for statistic in primary.metrics.available_fields & other.metrics.available_fields:
                primary_value = getattr(primary.metrics, statistic.value)
                other_value = getattr(other.metrics, statistic.value)
                tolerance = self._policy.float_tolerances.get(statistic, 0.0)
                if abs(primary_value - other_value) > tolerance:
                    conflicts.add(statistic)
        return frozenset(conflicts)
