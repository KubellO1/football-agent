"""按比赛编排主客两队阵容同步。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from app.core.exceptions import NotFoundError, ValidationError

if TYPE_CHECKING:
    from app.models.entities.team import Team
    from app.repositories.interfaces.fixture_repository import FixtureRepository
    from app.repositories.interfaces.reference import TeamRepository
    from app.services.player_squad_ingestion import PlayerSquadIngestionReport


class TeamSquadSynchronizer(Protocol):
    """编排层需要的最小球队阵容同步契约。"""

    async def sync_team(
        self,
        *,
        team_external_id: str,
    ) -> PlayerSquadIngestionReport: ...


@dataclass(frozen=True, slots=True)
class FixtureSquadIngestionReport:
    """一次比赛主客队阵容同步的汇总结果。"""

    source: str
    fixture_external_id: str
    home_team: PlayerSquadIngestionReport
    away_team: PlayerSquadIngestionReport

    @property
    def records_received(self) -> int:
        return self.home_team.records_received + self.away_team.records_received

    @property
    def records_created(self) -> int:
        return self.home_team.records_created + self.away_team.records_created

    @property
    def records_updated(self) -> int:
        return self.home_team.records_updated + self.away_team.records_updated

    @property
    def records_unchanged(self) -> int:
        return self.home_team.records_unchanged + self.away_team.records_unchanged


class FixtureSquadIngestionService:
    """先验证比赛与双方身份，再按主队、客队顺序同步阵容。"""

    def __init__(
        self,
        *,
        fixtures: FixtureRepository,
        teams: TeamRepository,
        squads: TeamSquadSynchronizer,
        source: str,
    ) -> None:
        normalized_source = source.strip()
        if not normalized_source:
            raise ValueError("source cannot be empty")
        self._fixtures = fixtures
        self._teams = teams
        self._squads = squads
        self._source = normalized_source

    async def sync_fixture(
        self,
        *,
        fixture_external_id: str,
    ) -> FixtureSquadIngestionReport:
        requested_id = fixture_external_id.strip()
        if not requested_id:
            raise ValueError("fixture_external_id cannot be empty")

        fixture = await self._fixtures.get_by_external_id(self._source, requested_id)
        if fixture is None:
            raise NotFoundError(
                f"fixture not found for {self._source}:{requested_id}",
            )

        teams = await self._teams.list_by_ids(
            [fixture.home_team_id, fixture.away_team_id],
        )
        teams_by_id = {team.id: team for team in teams}
        home_team = teams_by_id.get(fixture.home_team_id)
        away_team = teams_by_id.get(fixture.away_team_id)
        if home_team is None:
            raise NotFoundError("fixture home team not found")
        if away_team is None:
            raise NotFoundError("fixture away team not found")

        home_external_id = self._validated_external_id(home_team, side="home")
        away_external_id = self._validated_external_id(away_team, side="away")

        home_report = await self._squads.sync_team(
            team_external_id=home_external_id,
        )
        away_report = await self._squads.sync_team(
            team_external_id=away_external_id,
        )
        return FixtureSquadIngestionReport(
            source=self._source,
            fixture_external_id=requested_id,
            home_team=home_report,
            away_team=away_report,
        )

    def _validated_external_id(self, team: Team, *, side: str) -> str:
        external_source = team.external_source.strip() if team.external_source else None
        external_id = team.external_id.strip() if team.external_id else None
        if external_source != self._source or not external_id:
            raise ValidationError(
                f"fixture {side} team lacks a verified {self._source} identity",
            )
        return external_id
