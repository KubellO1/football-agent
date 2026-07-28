"""从持久化快照中选择可追溯、可供模型使用的统计数据。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from typing import TYPE_CHECKING

from app.models.value_objects.decision import EvidenceLevel

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from app.models.entities.team_match_statistics import TeamMatchStatistics
    from app.models.value_objects.data_quality import DataQualityAssessment
    from app.repositories.interfaces.team_match_statistics_repository import (
        TeamMatchStatisticsRepository,
    )
    from app.services.data_quality import DataQualityEvaluator


class VerificationStatus(StrEnum):
    """统计供数判定状态。"""

    VERIFIED = "verified"
    REJECTED = "rejected"
    NOT_FOUND = "not_found"


@dataclass(frozen=True, slots=True)
class VerifiedStatisticsPolicy:
    """来源选择与质量准入规则。来源顺序即权威优先级。"""

    source_priority: tuple[str, ...]
    minimum_completeness: float = 90.0
    minimum_evidence: EvidenceLevel = EvidenceLevel.B

    def __post_init__(self) -> None:
        if not self.source_priority:
            raise ValueError("source_priority cannot be empty")
        if len(set(self.source_priority)) != len(self.source_priority):
            raise ValueError("source_priority cannot contain duplicates")
        if any(not source or source != source.strip() for source in self.source_priority):
            raise ValueError("source_priority entries must be non-empty and trimmed")
        if (
            isinstance(self.minimum_completeness, bool)
            or not isfinite(self.minimum_completeness)
            or not 0.0 <= self.minimum_completeness <= 100.0
        ):
            raise ValueError("minimum_completeness must be between 0 and 100")
        if not isinstance(self.minimum_evidence, EvidenceLevel):
            raise ValueError("minimum_evidence must be an EvidenceLevel member")


@dataclass(frozen=True, slots=True)
class VerifiedStatisticsResult:
    """保留选择和拒绝证据，供后续决策日志使用。"""

    status: VerificationStatus
    snapshot: TeamMatchStatistics | None
    assessment: DataQualityAssessment | None
    considered_sources: tuple[str, ...] = ()
    ignored_sources: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        has_payload = self.snapshot is not None and self.assessment is not None
        if self.status is VerificationStatus.NOT_FOUND and (
            self.snapshot is not None or self.assessment is not None
        ):
            raise ValueError("not-found result cannot contain snapshot or assessment")
        if self.status is not VerificationStatus.NOT_FOUND and not has_payload:
            raise ValueError("verified or rejected result requires snapshot and assessment")

    @property
    def accepted(self) -> bool:
        return self.status is VerificationStatus.VERIFIED


class VerifiedStatisticsService:
    """按 as-of 时点从白名单来源中选择主快照并执行质量验证。"""

    def __init__(
        self,
        *,
        repository: TeamMatchStatisticsRepository,
        evaluator: DataQualityEvaluator,
        policy: VerifiedStatisticsPolicy,
    ) -> None:
        self._repository = repository
        self._evaluator = evaluator
        self._policy = policy

    async def verify(
        self,
        fixture_id: UUID,
        team_id: UUID,
        *,
        as_of: datetime,
    ) -> VerifiedStatisticsResult:
        """返回可审计的供数判定；任何质量失败都不会输出已验证状态。"""
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")

        snapshots = await self._repository.list_by_fixture(
            fixture_id,
            team_id=team_id,
            as_of=as_of,
        )
        if any(snapshot.captured_at > as_of for snapshot in snapshots):
            raise ValueError("repository returned a snapshot later than as_of")

        latest_by_source: dict[str, TeamMatchStatistics] = {}
        for snapshot in snapshots:
            current = latest_by_source.get(snapshot.source)
            if current is None or snapshot.captured_at > current.captured_at:
                latest_by_source[snapshot.source] = snapshot

        allowed_sources = set(self._policy.source_priority)
        ignored_sources = tuple(sorted(set(latest_by_source) - allowed_sources))
        considered_sources = tuple(
            source for source in self._policy.source_priority if source in latest_by_source
        )
        if not considered_sources:
            return VerifiedStatisticsResult(
                status=VerificationStatus.NOT_FOUND,
                snapshot=None,
                assessment=None,
                ignored_sources=ignored_sources,
            )

        primary_source = considered_sources[0]
        primary = latest_by_source[primary_source]
        corroborating = [latest_by_source[source] for source in considered_sources[1:]]
        assessment = self._evaluator.evaluate(
            primary,
            evaluated_at=as_of,
            corroborating=corroborating,
        )
        accepted = assessment.is_usable(
            minimum_completeness=self._policy.minimum_completeness,
            minimum_evidence=self._policy.minimum_evidence,
        )
        return VerifiedStatisticsResult(
            status=(VerificationStatus.VERIFIED if accepted else VerificationStatus.REJECTED),
            snapshot=primary,
            assessment=assessment,
            considered_sources=considered_sources,
            ignored_sources=ignored_sources,
        )
