"""按决策时间读取并验证官方首发阵容。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from app.models.value_objects.decision import EvidenceLevel
from app.models.value_objects.lineup import LineupStatus

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from app.models.entities.fixture import Fixture
    from app.models.entities.lineup import Lineup
    from app.repositories.interfaces.lineup_repository import LineupRepository


class LineupVerificationIssue(StrEnum):
    """单支球队首发阵容的拒绝原因。"""

    MISSING = "missing"
    FIXTURE_MISMATCH = "fixture_mismatch"
    TEAM_MISMATCH = "team_mismatch"
    SOURCE_MISMATCH = "source_mismatch"
    NOT_CONFIRMED = "not_confirmed"
    FUTURE_SNAPSHOT = "future_snapshot"
    EVIDENCE_BELOW_MINIMUM = "evidence_below_minimum"


@dataclass(frozen=True, slots=True)
class VerifiedLineupPolicy:
    """首发来源白名单与最低证据等级。"""

    source: str = "api-football"
    minimum_evidence: EvidenceLevel = EvidenceLevel.B

    def __post_init__(self) -> None:
        if not self.source or self.source != self.source.strip():
            raise ValueError("source must be non-empty and trimmed")
        if not isinstance(self.minimum_evidence, EvidenceLevel):
            raise ValueError("minimum_evidence must be an EvidenceLevel member")


@dataclass(frozen=True, slots=True)
class VerifiedTeamLineupResult:
    """单支球队在指定时间点的首发验证结果。"""

    team_id: UUID
    lineup: Lineup | None
    issues: tuple[LineupVerificationIssue, ...] = ()

    @property
    def accepted(self) -> bool:
        return self.lineup is not None and not self.issues


@dataclass(frozen=True, slots=True)
class VerifiedFixtureLineups:
    """一场比赛主客队首发的可审计验证结果。"""

    fixture_id: UUID
    as_of: datetime
    home: VerifiedTeamLineupResult
    away: VerifiedTeamLineupResult

    @property
    def accepted(self) -> bool:
        return self.home.accepted and self.away.accepted


class VerifiedLineupService:
    """只读取决策时间点之前、来源可信且已确认的主客队首发。"""

    def __init__(
        self,
        *,
        repository: LineupRepository,
        policy: VerifiedLineupPolicy | None = None,
    ) -> None:
        self._repository = repository
        self._policy = policy or VerifiedLineupPolicy()

    async def verify(
        self,
        fixture: Fixture,
        *,
        as_of: datetime,
    ) -> VerifiedFixtureLineups:
        """读取主客队首发；不回退到预测阵容，也不读取未来快照。"""
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")

        home = await self._verify_team(
            fixture_id=fixture.id,
            team_id=fixture.home_team_id,
            as_of=as_of,
        )
        away = await self._verify_team(
            fixture_id=fixture.id,
            team_id=fixture.away_team_id,
            as_of=as_of,
        )
        return VerifiedFixtureLineups(
            fixture_id=fixture.id,
            as_of=as_of,
            home=home,
            away=away,
        )

    async def _verify_team(
        self,
        *,
        fixture_id: UUID,
        team_id: UUID,
        as_of: datetime,
    ) -> VerifiedTeamLineupResult:
        lineup = await self._repository.get_latest_by_source(
            fixture_id,
            team_id,
            self._policy.source,
            status=LineupStatus.CONFIRMED,
            as_of=as_of,
        )
        if lineup is None:
            return VerifiedTeamLineupResult(
                team_id=team_id,
                lineup=None,
                issues=(LineupVerificationIssue.MISSING,),
            )

        issues: list[LineupVerificationIssue] = []
        if lineup.fixture_id != fixture_id:
            issues.append(LineupVerificationIssue.FIXTURE_MISMATCH)
        if lineup.team_id != team_id:
            issues.append(LineupVerificationIssue.TEAM_MISMATCH)
        if lineup.source.name != self._policy.source:
            issues.append(LineupVerificationIssue.SOURCE_MISMATCH)
        if not lineup.is_confirmed:
            issues.append(LineupVerificationIssue.NOT_CONFIRMED)
        if lineup.captured_at > as_of:
            issues.append(LineupVerificationIssue.FUTURE_SNAPSHOT)
        if not lineup.source.evidence_level.meets_minimum(self._policy.minimum_evidence):
            issues.append(LineupVerificationIssue.EVIDENCE_BELOW_MINIMUM)

        return VerifiedTeamLineupResult(
            team_id=team_id,
            lineup=lineup,
            issues=tuple(issues),
        )
