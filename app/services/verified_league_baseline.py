"""从已验证的主客队单场统计构建同赛季联赛 xG 基准。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from typing import TYPE_CHECKING

from app.models.entities.enums import MatchStatus
from app.models.value_objects.decision import DataCompleteness, EvidenceLevel

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from app.models.entities.fixture import Fixture
    from app.models.entities.team_match_statistics import TeamMatchStatistics
    from app.models.value_objects.data_quality import DataQualityAssessment
    from app.repositories.interfaces.fixture_repository import FixtureRepository
    from app.services.verified_statistics import VerifiedStatisticsResult, VerifiedStatisticsService


class LeagueBaselineStatus(StrEnum):
    """联赛 xG 基准构建状态。"""

    VERIFIED = "verified"
    INSUFFICIENT_HISTORY = "insufficient_history"
    QUALITY_REJECTED = "quality_rejected"


@dataclass(frozen=True, slots=True)
class LeagueXGBaseline:
    """同口径的每队每场平均 xG，不与实际进球基准混用。"""

    xg_per_team_match: float
    fixture_count: int

    def __post_init__(self) -> None:
        if not isfinite(self.xg_per_team_match) or self.xg_per_team_match <= 0.0:
            raise ValueError("xg_per_team_match must be finite and positive")
        if (
            not isinstance(self.fixture_count, int)
            or isinstance(self.fixture_count, bool)
            or self.fixture_count <= 0
        ):
            raise ValueError("fixture_count must be a positive integer")


@dataclass(frozen=True, slots=True)
class VerifiedLeagueBaselinePolicy:
    """联赛历史窗口、质量阈值与主客数据闭环规则。"""

    maximum_fixtures: int = 100
    minimum_fixtures: int = 30
    minimum_completeness: float = 90.0
    minimum_evidence: EvidenceLevel = EvidenceLevel.B
    pair_tolerance: float = 0.1

    def __post_init__(self) -> None:
        self._validate_positive_integer("maximum_fixtures", self.maximum_fixtures)
        self._validate_positive_integer("minimum_fixtures", self.minimum_fixtures)
        if self.minimum_fixtures > self.maximum_fixtures:
            raise ValueError("minimum_fixtures cannot exceed maximum_fixtures")
        if (
            isinstance(self.minimum_completeness, bool)
            or not isfinite(self.minimum_completeness)
            or not 0.0 <= self.minimum_completeness <= 100.0
        ):
            raise ValueError("minimum_completeness must be between 0 and 100")
        if not isinstance(self.minimum_evidence, EvidenceLevel):
            raise ValueError("minimum_evidence must be an EvidenceLevel member")
        if (
            isinstance(self.pair_tolerance, bool)
            or not isfinite(self.pair_tolerance)
            or self.pair_tolerance < 0.0
        ):
            raise ValueError("pair_tolerance must be finite and non-negative")

    @staticmethod
    def _validate_positive_integer(name: str, value: int) -> None:
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True, slots=True)
class VerifiedLeagueBaselineResult:
    """可审计的联赛基准结果；拒绝时不暴露部分基准。"""

    status: LeagueBaselineStatus
    baseline: LeagueXGBaseline | None
    data_completeness: DataCompleteness
    evidence_level: EvidenceLevel | None
    maximum_fixtures: int
    minimum_fixtures: int
    history_count: int
    verified_fixture_count: int
    rejected_fixture_ids: tuple[UUID, ...] = ()

    def __post_init__(self) -> None:
        if not 0 <= self.verified_fixture_count <= self.history_count <= self.maximum_fixtures:
            raise ValueError("baseline counts must satisfy verified <= history <= maximum")
        if not 0 < self.minimum_fixtures <= self.maximum_fixtures:
            raise ValueError("minimum_fixtures must be within the maximum")
        if len(self.rejected_fixture_ids) != self.history_count - self.verified_fixture_count:
            raise ValueError("rejected fixture count does not match baseline counts")
        if len(set(self.rejected_fixture_ids)) != len(self.rejected_fixture_ids):
            raise ValueError("rejected fixture ids cannot contain duplicates")
        if self.verified_fixture_count == 0 and self.evidence_level is not None:
            raise ValueError("empty verified history cannot have an evidence level")
        if self.verified_fixture_count > 0 and self.evidence_level is None:
            raise ValueError("verified history requires an evidence level")
        if self.status is LeagueBaselineStatus.VERIFIED:
            if self.baseline is None:
                raise ValueError("verified result requires a baseline")
            if self.baseline.fixture_count != self.verified_fixture_count:
                raise ValueError("baseline must contain every verified fixture")
        elif self.baseline is not None:
            raise ValueError("rejected result cannot expose a partial baseline")

    @property
    def accepted(self) -> bool:
        return self.status is LeagueBaselineStatus.VERIFIED


@dataclass(frozen=True, slots=True)
class _VerifiedSide:
    snapshot: TeamMatchStatistics
    assessment: DataQualityAssessment


@dataclass(frozen=True, slots=True)
class _VerifiedPair:
    fixture: Fixture
    home: _VerifiedSide
    away: _VerifiedSide


class VerifiedLeagueBaselineBuilder:
    """构建固定窗口联赛 xG 基准，不使用更老比赛替换被拒样本。"""

    def __init__(
        self,
        *,
        fixture_repository: FixtureRepository,
        statistics_service: VerifiedStatisticsService,
        policy: VerifiedLeagueBaselinePolicy | None = None,
    ) -> None:
        self._fixture_repository = fixture_repository
        self._statistics_service = statistics_service
        self._policy = policy or VerifiedLeagueBaselinePolicy()

    async def build(
        self,
        competition_id: UUID,
        season_id: UUID,
        *,
        target_fixture_id: UUID,
        as_of: datetime,
    ) -> VerifiedLeagueBaselineResult:
        """按同一赛前时点验证主客双方快照并计算每队每场平均 xG。"""
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")

        fixtures = await self._fixture_repository.list_finished_by_competition(
            competition_id,
            season_id=season_id,
            limit=self._policy.maximum_fixtures,
            exclude_fixture_id=target_fixture_id,
            before=as_of,
        )
        self._validate_fixture_set(
            fixtures,
            competition_id=competition_id,
            season_id=season_id,
            target_fixture_id=target_fixture_id,
            as_of=as_of,
        )

        verified: list[_VerifiedPair] = []
        rejected_ids: list[UUID] = []
        for fixture in fixtures:
            if fixture.score is None:
                rejected_ids.append(fixture.id)
                continue

            home = await self._verify_side(
                fixture_id=fixture.id,
                team_id=fixture.home_team_id,
                as_of=as_of,
            )
            if home is None:
                rejected_ids.append(fixture.id)
                continue
            away = await self._verify_side(
                fixture_id=fixture.id,
                team_id=fixture.away_team_id,
                as_of=as_of,
            )
            if away is None or not self._pair_is_consistent(home, away):
                rejected_ids.append(fixture.id)
                continue
            verified.append(
                _VerifiedPair(
                    fixture=fixture,
                    home=home,
                    away=away,
                )
            )

        history_count = len(fixtures)
        verified_count = len(verified)
        completeness = self._aggregate_completeness(verified, history_count=history_count)
        evidence_level = self._lowest_evidence(verified)
        xg_rate = self._xg_rate(verified)

        if history_count < self._policy.minimum_fixtures:
            status = LeagueBaselineStatus.INSUFFICIENT_HISTORY
        elif (
            verified_count < self._policy.minimum_fixtures
            or not completeness.is_sufficient(self._policy.minimum_completeness)
            or evidence_level is None
            or not evidence_level.meets_minimum(self._policy.minimum_evidence)
            or xg_rate is None
            or xg_rate <= 0.0
        ):
            status = LeagueBaselineStatus.QUALITY_REJECTED
        else:
            status = LeagueBaselineStatus.VERIFIED

        baseline = (
            LeagueXGBaseline(
                xg_per_team_match=xg_rate,
                fixture_count=verified_count,
            )
            if status is LeagueBaselineStatus.VERIFIED and xg_rate is not None
            else None
        )
        return VerifiedLeagueBaselineResult(
            status=status,
            baseline=baseline,
            data_completeness=completeness,
            evidence_level=evidence_level,
            maximum_fixtures=self._policy.maximum_fixtures,
            minimum_fixtures=self._policy.minimum_fixtures,
            history_count=history_count,
            verified_fixture_count=verified_count,
            rejected_fixture_ids=tuple(rejected_ids),
        )

    def _validate_fixture_set(
        self,
        fixtures: list[Fixture],
        *,
        competition_id: UUID,
        season_id: UUID,
        target_fixture_id: UUID,
        as_of: datetime,
    ) -> None:
        if len(fixtures) > self._policy.maximum_fixtures:
            raise ValueError("repository returned more fixtures than requested")
        fixture_ids = [fixture.id for fixture in fixtures]
        if len(set(fixture_ids)) != len(fixture_ids):
            raise ValueError("repository returned duplicate fixtures")
        if target_fixture_id in fixture_ids:
            raise ValueError("repository returned the excluded target fixture")

        for fixture in fixtures:
            if fixture.status is not MatchStatus.FINISHED:
                raise ValueError("repository returned a fixture that is not finished")
            if fixture.competition_id != competition_id or fixture.season_id != season_id:
                raise ValueError("repository returned a fixture outside the requested season")
            if fixture.kickoff.tzinfo is None or fixture.kickoff.utcoffset() is None:
                raise ValueError("repository returned a fixture with a naive kickoff")
            if fixture.kickoff >= as_of:
                raise ValueError("repository returned a fixture that is not earlier than as_of")

        expected_ids = [
            fixture.id
            for fixture in sorted(
                fixtures,
                key=lambda fixture: (fixture.kickoff, fixture.id),
                reverse=True,
            )
        ]
        if fixture_ids != expected_ids:
            raise ValueError("repository returned fixtures outside deterministic descending order")

    async def _verify_side(
        self,
        *,
        fixture_id: UUID,
        team_id: UUID,
        as_of: datetime,
    ) -> _VerifiedSide | None:
        result = await self._statistics_service.verify(
            fixture_id,
            team_id,
            as_of=as_of,
        )
        self._validate_snapshot_identity(
            result,
            fixture_id=fixture_id,
            team_id=team_id,
        )
        if (
            not result.accepted
            or result.snapshot is None
            or result.assessment is None
            or result.snapshot.metrics.xg is None
            or result.snapshot.metrics.xg_against is None
        ):
            return None
        return _VerifiedSide(
            snapshot=result.snapshot,
            assessment=result.assessment,
        )

    @staticmethod
    def _validate_snapshot_identity(
        result: VerifiedStatisticsResult,
        *,
        fixture_id: UUID,
        team_id: UUID,
    ) -> None:
        if result.snapshot is not None and (
            result.snapshot.fixture_id != fixture_id or result.snapshot.team_id != team_id
        ):
            raise ValueError("statistics service returned an unrelated snapshot")

    def _pair_is_consistent(self, home: _VerifiedSide, away: _VerifiedSide) -> bool:
        home_metrics = home.snapshot.metrics
        away_metrics = away.snapshot.metrics
        if (
            home_metrics.xg is None
            or home_metrics.xg_against is None
            or away_metrics.xg is None
            or away_metrics.xg_against is None
        ):
            return False
        tolerance = self._policy.pair_tolerance + 1e-12
        return (
            abs(home_metrics.xg - away_metrics.xg_against) <= tolerance
            and abs(away_metrics.xg - home_metrics.xg_against) <= tolerance
        )

    @staticmethod
    def _aggregate_completeness(
        verified: list[_VerifiedPair],
        *,
        history_count: int,
    ) -> DataCompleteness:
        if history_count == 0:
            return DataCompleteness(0.0)
        quality_sum = sum(
            pair.home.assessment.completeness.value + pair.away.assessment.completeness.value
            for pair in verified
        )
        return DataCompleteness(quality_sum / (2 * history_count))

    @staticmethod
    def _lowest_evidence(verified: list[_VerifiedPair]) -> EvidenceLevel | None:
        if not verified:
            return None
        return min(
            (
                side.assessment.evidence_level
                for pair in verified
                for side in (pair.home, pair.away)
            ),
            key=lambda level: level.rank,
        )

    @staticmethod
    def _xg_rate(verified: list[_VerifiedPair]) -> float | None:
        if not verified:
            return None
        total_xg = 0.0
        for pair in verified:
            home_xg = pair.home.snapshot.metrics.xg
            away_xg = pair.away.snapshot.metrics.xg
            if home_xg is None or away_xg is None:
                raise ValueError("verified pair must have home and away xG")
            total_xg += home_xg + away_xg
        return total_xg / (2 * len(verified))
