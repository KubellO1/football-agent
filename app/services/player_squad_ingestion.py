"""球队阵容球员身份的验证与幂等持久化编排。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from app.core.exceptions import ExternalServiceError, NotFoundError, ValidationError
from app.models.entities.enums import PlayerPosition
from app.models.entities.player import Player

if TYPE_CHECKING:
    from uuid import UUID

    from app.providers.interfaces.player_squad_provider import PlayerSquadProvider
    from app.providers.schemas.player_squad import ProviderSquadBatch, ProviderSquadPlayer
    from app.repositories.interfaces.player_repository import PlayerRepository
    from app.repositories.interfaces.reference import TeamRepository


@dataclass(frozen=True, slots=True)
class PlayerSquadIngestionReport:
    """一次球队阵容身份同步的结果。"""

    source: str
    team_external_id: str
    records_received: int
    records_created: int
    records_updated: int
    records_unchanged: int


@dataclass(frozen=True, slots=True)
class _PreparedPlayer:
    record: ProviderSquadPlayer
    position: PlayerPosition
    existing: Player | None


class PlayerSquadIngestionService:
    """先验证完整批次，再幂等创建或更新球员主数据。"""

    _POSITION_MAP = {
        "attacker": PlayerPosition.FORWARD,
        "defender": PlayerPosition.DEFENDER,
        "forward": PlayerPosition.FORWARD,
        "goalkeeper": PlayerPosition.GOALKEEPER,
        "midfielder": PlayerPosition.MIDFIELDER,
    }

    def __init__(
        self,
        *,
        provider: PlayerSquadProvider,
        teams: TeamRepository,
        players: PlayerRepository,
        source: str,
    ) -> None:
        normalized_source = source.strip()
        if not normalized_source:
            raise ValueError("source cannot be empty")
        self._provider = provider
        self._teams = teams
        self._players = players
        self._source = normalized_source

    async def sync_team(
        self,
        *,
        team_external_id: str,
    ) -> PlayerSquadIngestionReport:
        requested_id = team_external_id.strip()
        if not requested_id:
            raise ValueError("team_external_id cannot be empty")

        team = await self._teams.get_by_external_id(self._source, requested_id)
        if team is None:
            raise NotFoundError(
                f"team not found for {self._source}:{requested_id}",
            )

        batch = await self._provider.get_team_squad(
            team_external_id=requested_id,
        )
        self._validate_batch(batch, requested_id=requested_id)
        prepared = await self._prepare_all(batch)

        created = 0
        updated = 0
        unchanged = 0
        for item in prepared:
            if item.existing is None:
                await self._players.add(
                    self._new_player(item, team_id=team.id),
                )
                created += 1
            elif self._has_changes(item, team_id=team.id):
                await self._players.update(
                    replace(
                        item.existing,
                        name=item.record.player_name,
                        position=item.position,
                        team_id=team.id,
                    ),
                )
                updated += 1
            else:
                unchanged += 1

        return PlayerSquadIngestionReport(
            source=self._source,
            team_external_id=requested_id,
            records_received=len(batch.records),
            records_created=created,
            records_updated=updated,
            records_unchanged=unchanged,
        )

    def _validate_batch(
        self,
        batch: ProviderSquadBatch,
        *,
        requested_id: str,
    ) -> None:
        if batch.source != self._source:
            raise ValidationError(
                f"provider source mismatch: expected {self._source}, got {batch.source}",
            )
        if batch.team_external_id != requested_id:
            raise ValidationError("provider team id does not match request")
        if not batch.response_complete:
            raise ExternalServiceError("player squad response is incomplete")

        player_ids = [record.player_external_id for record in batch.records]
        if len(player_ids) != len(set(player_ids)):
            raise ValidationError("player squad contains duplicate player ids")

    async def _prepare_all(
        self,
        batch: ProviderSquadBatch,
    ) -> list[_PreparedPlayer]:
        prepared: list[_PreparedPlayer] = []
        for record in batch.records:
            position = self._map_position(record.raw_position)
            existing = await self._players.get_by_external_id(
                self._source,
                record.player_external_id,
            )
            prepared.append(
                _PreparedPlayer(
                    record=record,
                    position=position,
                    existing=existing,
                ),
            )
        return prepared

    def _new_player(self, item: _PreparedPlayer, *, team_id: UUID) -> Player:
        return Player(
            name=item.record.player_name,
            position=item.position,
            team_id=team_id,
            external_source=self._source,
            external_id=item.record.player_external_id,
        )

    @staticmethod
    def _has_changes(item: _PreparedPlayer, *, team_id: UUID) -> bool:
        assert item.existing is not None
        return (
            item.existing.name != item.record.player_name
            or item.existing.position is not item.position
            or item.existing.team_id != team_id
        )

    @classmethod
    def _map_position(cls, raw_position: str) -> PlayerPosition:
        normalized = " ".join(raw_position.lower().split())
        position = cls._POSITION_MAP.get(normalized)
        if position is None:
            raise ValidationError(f"unsupported player position: {raw_position}")
        return position
