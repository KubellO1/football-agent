"""比赛官方阵容的验证、身份解析与幂等持久化编排。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.core.exceptions import ExternalServiceError, NotFoundError, ValidationError
from app.models.entities.lineup import Lineup
from app.models.value_objects.lineup import Formation, LineupSource, LineupStatus

if TYPE_CHECKING:
    from uuid import UUID

    from app.models.entities.fixture import Fixture
    from app.models.entities.player import Player
    from app.models.entities.team import Team
    from app.models.value_objects.decision import EvidenceLevel
    from app.providers.interfaces.fixture_lineup_provider import FixtureLineupProvider
    from app.providers.schemas.fixture_lineup import (
        ProviderFixtureLineupBatch,
        ProviderTeamLineup,
    )
    from app.repositories.interfaces.fixture_repository import FixtureRepository
    from app.repositories.interfaces.lineup_repository import LineupRepository
    from app.repositories.interfaces.player_repository import PlayerRepository
    from app.repositories.interfaces.reference import TeamRepository


@dataclass(frozen=True, slots=True)
class FixtureLineupIngestionReport:
    """一次比赛阵容同步的审计摘要。"""

    source: str
    fixture_external_id: str
    lineups_received: int
    players_received: int
    lineups_created: int
    lineups_unchanged: int


class FixtureLineupIngestionService:
    """在所有外部身份验证成功后，追加保存主客队阵容快照。"""

    def __init__(
        self,
        *,
        provider: FixtureLineupProvider,
        fixtures: FixtureRepository,
        teams: TeamRepository,
        players: PlayerRepository,
        lineups: LineupRepository,
        source: str,
        evidence_level: EvidenceLevel,
    ) -> None:
        normalized_source = source.strip()
        if not normalized_source:
            raise ValueError("source cannot be empty")
        self._provider = provider
        self._fixtures = fixtures
        self._teams = teams
        self._players = players
        self._lineups = lineups
        self._source = normalized_source
        self._evidence_level = evidence_level

    async def sync_fixture(
        self,
        *,
        fixture_external_id: str,
    ) -> FixtureLineupIngestionReport:
        requested_id = fixture_external_id.strip()
        if not requested_id:
            raise ValueError("fixture_external_id cannot be empty")

        fixture = await self._fixtures.get_by_external_id(self._source, requested_id)
        if fixture is None:
            raise NotFoundError(f"fixture not found for {self._source}:{requested_id}")

        teams_by_external_id = await self._verified_fixture_teams(fixture)
        batch = await self._provider.get_fixture_lineups(
            fixture_external_id=requested_id,
        )
        self._validate_batch(batch, requested_id=requested_id)
        prepared = await self._prepare_all(
            batch,
            fixture=fixture,
            teams_by_external_id=teams_by_external_id,
        )

        created = 0
        unchanged = 0
        for lineup in prepared:
            if await self._lineups.add_if_absent(lineup):
                created += 1
            else:
                unchanged += 1

        return FixtureLineupIngestionReport(
            source=self._source,
            fixture_external_id=requested_id,
            lineups_received=len(batch.lineups),
            players_received=sum(
                len(team_lineup.starting) + len(team_lineup.substitutes)
                for team_lineup in batch.lineups
            ),
            lineups_created=created,
            lineups_unchanged=unchanged,
        )

    async def _verified_fixture_teams(self, fixture: Fixture) -> dict[str, Team]:
        teams = await self._teams.list_by_ids(
            [fixture.home_team_id, fixture.away_team_id],
        )
        teams_by_id = {team.id: team for team in teams}
        verified: dict[str, Team] = {}
        for side, team_id in (
            ("home", fixture.home_team_id),
            ("away", fixture.away_team_id),
        ):
            team = teams_by_id.get(team_id)
            if team is None:
                raise NotFoundError(f"fixture {side} team not found")
            external_source = team.external_source.strip() if team.external_source else None
            external_id = team.external_id.strip() if team.external_id else None
            if external_source != self._source or not external_id:
                raise ValidationError(
                    f"fixture {side} team lacks a verified {self._source} identity",
                )
            if external_id in verified:
                raise ValidationError("fixture teams share the same external identity")
            verified[external_id] = team
        return verified

    def _validate_batch(
        self,
        batch: ProviderFixtureLineupBatch,
        *,
        requested_id: str,
    ) -> None:
        if batch.source != self._source:
            raise ValidationError(
                f"provider source mismatch: expected {self._source}, got {batch.source}",
            )
        if batch.fixture_external_id != requested_id:
            raise ValidationError("provider fixture id does not match request")
        if not batch.response_complete:
            raise ExternalServiceError("fixture lineup response is incomplete")

    async def _prepare_all(
        self,
        batch: ProviderFixtureLineupBatch,
        *,
        fixture: Fixture,
        teams_by_external_id: dict[str, Team],
    ) -> list[Lineup]:
        received_team_ids = {item.team_external_id for item in batch.lineups}
        expected_team_ids = set(teams_by_external_id)
        if received_team_ids and received_team_ids != expected_team_ids:
            raise ValidationError("provider lineup teams do not match fixture teams")

        prepared: list[Lineup] = []
        for item in batch.lineups:
            team = teams_by_external_id[item.team_external_id]
            local_players = await self._players.list_by_team(team.id)
            players_by_external_id = self._verified_player_identities(local_players)
            prepared.append(
                self._to_domain(
                    item,
                    fixture_id=fixture.id,
                    team_id=team.id,
                    players_by_external_id=players_by_external_id,
                    batch=batch,
                ),
            )
        return prepared

    def _verified_player_identities(self, players: list[Player]) -> dict[str, UUID]:
        verified: dict[str, UUID] = {}
        for player in players:
            external_source = player.external_source.strip() if player.external_source else None
            external_id = player.external_id.strip() if player.external_id else None
            if external_source != self._source or not external_id:
                continue
            if external_id in verified:
                raise ValidationError(f"duplicate local player identity: {external_id}")
            verified[external_id] = player.id
        return verified

    def _to_domain(
        self,
        item: ProviderTeamLineup,
        *,
        fixture_id: UUID,
        team_id: UUID,
        players_by_external_id: dict[str, UUID],
        batch: ProviderFixtureLineupBatch,
    ) -> Lineup:
        external_ids = [player.player_external_id for player in (*item.starting, *item.substitutes)]
        missing = [
            external_id for external_id in external_ids if external_id not in players_by_external_id
        ]
        if missing:
            joined = ", ".join(missing)
            raise ValidationError(
                f"lineup contains unverified player identities for team "
                f"{item.team_external_id}: {joined}",
            )

        try:
            return Lineup(
                fixture_id=fixture_id,
                team_id=team_id,
                status=LineupStatus.CONFIRMED,
                source=LineupSource(
                    name=batch.source,
                    evidence_level=self._evidence_level,
                    reference=batch.request_reference,
                ),
                starting=tuple(
                    players_by_external_id[player.player_external_id] for player in item.starting
                ),
                substitutes=tuple(
                    players_by_external_id[player.player_external_id] for player in item.substitutes
                ),
                formation=Formation(item.formation) if item.formation is not None else None,
                captured_at=batch.captured_at,
            )
        except ValueError as exc:
            raise ValidationError("provider lineup failed domain validation") from exc
