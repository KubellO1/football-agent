"""从已验证的单场统计快照构建球队近期状态。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from typing import TYPE_CHECKING

from app.models.entities.enums import MatchStatus
from app.models.value_objects.decision import DataCompleteness, EvidenceLevel
from app.models.value_objects.statistics import TeamStatistics

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from app.models.entities.fixture import Fixture
    from app.models.entities.team_match_statistics import TeamMatchStatistics
    from app.models.value_objects.data_quality import DataQualityAssessment
    from app.repositories.interfaces.fixture_repository import FixtureRepository
    from app.services.verified_statistics import VerifiedStatisticsService


class TeamFormStatus(StrEnum):
    """球队近期状态构建结果。"""

    VERIFIED = "verified"
    INSUFFICIENT_HISTORY = "insufficient_history"
    QUALITY_REJECTED = "quality_rejected"


@dataclass(frozen=True, slots=True)
class VerifiedTeamFormPolicy:
    """近期窗口与聚合质量准入规则。"""

    window_size: int = 10
    minimum_completeness: float = 90.0
    minimum_evidence: EvidenceLevel = EvidenceLevel.B

    def __post_init__(self) -> None:
        if (
            not isinstance(self.window_size, int)
            or isinstance(self.window_size, bool)
            or self.window_size <= 0
        ):
            raise ValueError("window_size must be a positive integer")
        if (
            isinstance(self.minimum_completeness, bool)
            or not isfinite(self.minimum_completeness)
            or not 0.0 <= self.minimum_completeness <= 100.0
        ):
            raise ValueError("minimum_completeness must be between 0 and 100")
        if not isinstance(self.minimum_evidence, EvidenceLevel):
            raise ValueError("minimum_evidence must be an EvidenceLevel member")


@dataclass(frozen=True, slots=True)
class VerifiedTeamFormResult:
    """可审计的近期状态结果；拒绝时不暴露部分统计。"""

    status: TeamFormStatus
    statistics: TeamStatistics | None
    data_completeness: DataCompleteness
    evidence_level: EvidenceLevel | None
    requested_window: int
    history_count: int
    verified_count: int
    rejected_fixture_ids: tuple[UUID, ...] = ()

    def __post_init__(self) -> None:
        if self.requested_window <= 0:
            raise ValueError("requested_window must be positive")
        if not 0 <= self.verified_count <= self.history_count <= self.requested_window:
            raise ValueError("form counts must satisfy verified <= history <= requested")
        if len(self.rejected_fixture_ids) != self.history_count - self.verified_count:
            raise ValueError("rejected fixture count does not match form counts")
        if self.verified_count == 0 and self.evidence_level is not None:
            raise ValueError("empty verified history cannot have an evidence level")
        if self.verified_count > 0 and self.evidence_level is None:
            raise ValueError("verified history requires an evidence level")
        if self.status is TeamFormStatus.VERIFIED:
            if self.statistics is None:
                raise ValueError("verified result requires statistics")
            if self.statistics.matches_played != self.verified_count:
                raise ValueError("statistics must contain every verified fixture")
        elif self.statistics is not None:
            raise ValueError("rejected result cannot expose partial statistics")

    @property
    def accepted(self) -> bool:
        return self.status is TeamFormStatus.VERIFIED


@dataclass(frozen=True, slots=True)
class _VerifiedFixture:
    fixture: Fixture
    snapshot: TeamMatchStatistics
    assessment: DataQualityAssessment


class VerifiedTeamFormBuilder:
    """严格按赛前可见数据构建固定窗口球队状态，不用更老比赛补位。"""

    def __init__(
        self,
        *,
        fixture_repository: FixtureRepository,
        statistics_service: VerifiedStatisticsService,
        policy: VerifiedTeamFormPolicy | None = None,
    ) -> None:
        self._fixture_repository = fixture_repository
        self._statistics_service = statistics_service
        self._policy = policy or VerifiedTeamFormPolicy()

    async def build(
        self,
        team_id: UUID,
        *,
        target_fixture_id: UUID,
        as_of: datetime,
    ) -> VerifiedTeamFormResult:
        """构建赛前球队状态；所有快照均以同一 ``as_of`` 时点验证。"""
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")

        fixtures = await self._fixture_repository.list_finished_by_team(
            team_id,
            limit=self._policy.window_size,
            exclude_fixture_id=target_fixture_id,
            before=as_of,
        )
        if len(fixtures) > self._policy.window_size:
            raise ValueError("repository returned more fixtures than requested")
        fixture_ids = [fixture.id for fixture in fixtures]
        if len(set(fixture_ids)) != len(fixture_ids):
            raise ValueError("repository returned duplicate fixtures")
        if target_fixture_id in fixture_ids:
            raise ValueError("repository returned the excluded target fixture")

        verified: list[_VerifiedFixture] = []
        rejected_ids: list[UUID] = []
        for fixture in fixtures:
            self._validate_fixture(fixture, team_id=team_id, as_of=as_of)
            if fixture.score is None:
                rejected_ids.append(fixture.id)
                continue

            result = await self._statistics_service.verify(
                fixture.id,
                team_id,
                as_of=as_of,
            )
            if result.snapshot is not None and (
                result.snapshot.fixture_id != fixture.id or result.snapshot.team_id != team_id
            ):
                raise ValueError("statistics service returned an unrelated snapshot")
            if (
                not result.accepted
                or result.snapshot is None
                or result.assessment is None
                or result.snapshot.metrics.xg is None
                or result.snapshot.metrics.xg_against is None
            ):
                rejected_ids.append(fixture.id)
                continue
            verified.append(
                _VerifiedFixture(
                    fixture=fixture,
                    snapshot=result.snapshot,
                    assessment=result.assessment,
                )
            )

        history_count = len(fixtures)
        verified_count = len(verified)
        completeness = self._aggregate_completeness(verified)
        evidence_level = self._lowest_evidence(verified)
        maximum_history_completeness = history_count / self._policy.window_size * 100.0

        if maximum_history_completeness < self._policy.minimum_completeness:
            status = TeamFormStatus.INSUFFICIENT_HISTORY
        elif (
            not completeness.is_sufficient(self._policy.minimum_completeness)
            or evidence_level is None
            or not evidence_level.meets_minimum(self._policy.minimum_evidence)
        ):
            status = TeamFormStatus.QUALITY_REJECTED
        else:
            status = TeamFormStatus.VERIFIED

        statistics = self._aggregate_statistics(verified, team_id=team_id) if verified else None
        if status is not TeamFormStatus.VERIFIED:
            statistics = None

        return VerifiedTeamFormResult(
            status=status,
            statistics=statistics,
            data_completeness=completeness,
            evidence_level=evidence_level,
            requested_window=self._policy.window_size,
            history_count=history_count,
            verified_count=verified_count,
            rejected_fixture_ids=tuple(rejected_ids),
        )

    def _validate_fixture(self, fixture: Fixture, *, team_id: UUID, as_of: datetime) -> None:
        if fixture.status is not MatchStatus.FINISHED:
            raise ValueError("repository returned a fixture that is not finished")
        if fixture.kickoff.tzinfo is None or fixture.kickoff.utcoffset() is None:
            raise ValueError("repository returned a fixture with a naive kickoff")
        if fixture.kickoff >= as_of:
            raise ValueError("repository returned a fixture that is not earlier than as_of")
        if team_id not in (fixture.home_team_id, fixture.away_team_id):
            raise ValueError("repository returned a fixture unrelated to the requested team")

    def _aggregate_completeness(
        self,
        verified: list[_VerifiedFixture],
    ) -> DataCompleteness:
        if not verified:
            return DataCompleteness(0.0)
        quality_sum = sum(item.assessment.completeness.value for item in verified)
        return DataCompleteness(quality_sum / self._policy.window_size)

    @staticmethod
    def _lowest_evidence(verified: list[_VerifiedFixture]) -> EvidenceLevel | None:
        if not verified:
            return None
        return min(
            (item.assessment.evidence_level for item in verified),
            key=lambda level: level.rank,
        )

    @staticmethod
    def _aggregate_statistics(
        verified: list[_VerifiedFixture],
        *,
        team_id: UUID,
    ) -> TeamStatistics:
        wins = draws = losses = goals_for = goals_against = 0
        xg_for = xg_against = 0.0

        for item in verified:
            fixture = item.fixture
            score = fixture.score
            if score is None:
                raise ValueError("verified fixture must have a score")
            if fixture.home_team_id == team_id:
                fixture_goals_for, fixture_goals_against = score.home, score.away
            else:
                fixture_goals_for, fixture_goals_against = score.away, score.home

            goals_for += fixture_goals_for
            goals_against += fixture_goals_against
            if fixture_goals_for > fixture_goals_against:
                wins += 1
            elif fixture_goals_for == fixture_goals_against:
                draws += 1
            else:
                losses += 1

            metrics = item.snapshot.metrics
            if metrics.xg is None or metrics.xg_against is None:
                raise ValueError("verified fixture must have xG and xGA")
            xg_for += metrics.xg
            xg_against += metrics.xg_against

        return TeamStatistics(
            matches_played=len(verified),
            wins=wins,
            draws=draws,
            losses=losses,
            goals_for=goals_for,
            goals_against=goals_against,
            xg_for=xg_for,
            xg_against=xg_against,
        )
