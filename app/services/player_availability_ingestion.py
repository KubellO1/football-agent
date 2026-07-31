"""球员可用性数据的验证与持久化编排。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.core.exceptions import ExternalServiceError, NotFoundError, ValidationError
from app.models.entities.player_availability import PlayerAvailabilityObservation
from app.models.value_objects.availability import AvailabilitySource, AvailabilityStatus

if TYPE_CHECKING:
    from uuid import UUID

    from app.models.entities.fixture import Fixture
    from app.models.value_objects.decision import EvidenceLevel
    from app.providers.interfaces.player_availability_provider import (
        PlayerAvailabilityProvider,
    )
    from app.providers.schemas.player_availability import (
        ProviderAvailabilityBatch,
        ProviderPlayerAvailability,
    )
    from app.repositories.interfaces.fixture_repository import FixtureRepository
    from app.repositories.interfaces.player_availability_repository import (
        PlayerAvailabilityObservationRepository,
    )
    from app.repositories.interfaces.player_repository import PlayerRepository
    from app.repositories.interfaces.reference import TeamRepository


@dataclass(frozen=True, slots=True)
class AvailabilityIngestionReport:
    """一次球员可用性采集的持久化结果。"""

    source: str
    fixture_external_id: str
    records_received: int
    records_created: int
    duplicates_ignored: int


@dataclass(frozen=True, slots=True)
class _ResolvedRecord:
    record: ProviderPlayerAvailability
    team_id: UUID
    player_id: UUID


class PlayerAvailabilityIngestionService:
    """验证 Provider 批次中的全部身份后，幂等追加可用性观察。"""

    _STATUS_MAP = {
        "available": AvailabilityStatus.AVAILABLE,
        "doubtful": AvailabilityStatus.DOUBTFUL,
        "missing fixture": AvailabilityStatus.OUT,
        "out": AvailabilityStatus.OUT,
        "questionable": AvailabilityStatus.DOUBTFUL,
        "returned": AvailabilityStatus.RETURNED,
        "suspended": AvailabilityStatus.SUSPENDED,
    }

    def __init__(
        self,
        *,
        provider: PlayerAvailabilityProvider,
        fixtures: FixtureRepository,
        teams: TeamRepository,
        players: PlayerRepository,
        observations: PlayerAvailabilityObservationRepository,
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
        self._observations = observations
        self._source = normalized_source
        self._evidence_level = evidence_level

    async def sync_fixture(
        self,
        *,
        fixture_external_id: str,
    ) -> AvailabilityIngestionReport:
        requested_id = fixture_external_id.strip()
        if not requested_id:
            raise ValueError("fixture_external_id cannot be empty")

        batch = await self._provider.get_fixture_availability(
            fixture_external_id=requested_id,
        )
        self._validate_batch(batch, requested_id=requested_id)

        fixture = await self._fixtures.get_by_external_id(self._source, requested_id)
        if fixture is None:
            raise NotFoundError(
                f"fixture not found for {self._source}:{requested_id}",
            )

        resolved = await self._resolve_all(batch, fixture=fixture)
        observations = [
            self._to_observation(item, batch=batch, fixture=fixture) for item in resolved
        ]

        created = 0
        for observation in observations:
            if await self._observations.add_if_absent(observation):
                created += 1

        received = len(batch.records)
        return AvailabilityIngestionReport(
            source=self._source,
            fixture_external_id=requested_id,
            records_received=received,
            records_created=created,
            duplicates_ignored=received - created,
        )

    def _validate_batch(
        self,
        batch: ProviderAvailabilityBatch,
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
            raise ExternalServiceError("player availability response is incomplete")

    async def _resolve_all(
        self,
        batch: ProviderAvailabilityBatch,
        *,
        fixture: Fixture,
    ) -> list[_ResolvedRecord]:
        fixture_team_ids = {fixture.home_team_id, fixture.away_team_id}
        resolved: list[_ResolvedRecord] = []

        for record in batch.records:
            team = await self._teams.get_by_external_id(
                self._source,
                record.team_external_id,
            )
            if team is None:
                raise NotFoundError(
                    f"team not found for {self._source}:{record.team_external_id}",
                )
            if team.id not in fixture_team_ids:
                raise ValidationError("provider team does not belong to fixture")

            player = await self._players.get_by_external_id(
                self._source,
                record.player_external_id,
            )
            if player is None:
                raise NotFoundError(
                    f"player not found for {self._source}:{record.player_external_id}",
                )
            if player.team_id != team.id:
                raise ValidationError("provider player does not belong to provider team")

            resolved.append(
                _ResolvedRecord(
                    record=record,
                    team_id=team.id,
                    player_id=player.id,
                ),
            )

        return resolved

    def _to_observation(
        self,
        item: _ResolvedRecord,
        *,
        batch: ProviderAvailabilityBatch,
        fixture: Fixture,
    ) -> PlayerAvailabilityObservation:
        return PlayerAvailabilityObservation(
            fixture_id=fixture.id,
            team_id=item.team_id,
            player_id=item.player_id,
            status=self._map_status(item.record.raw_status),
            source=AvailabilitySource(
                name=self._source,
                evidence_level=self._evidence_level,
                reference=batch.request_reference,
            ),
            captured_at=batch.captured_at,
            source_updated_at=item.record.source_updated_at,
            reason=item.record.reason,
            expected_return=item.record.expected_return,
        )

    @classmethod
    def _map_status(cls, raw_status: str) -> AvailabilityStatus:
        normalized = " ".join(raw_status.lower().split())
        return cls._STATUS_MAP.get(normalized, AvailabilityStatus.UNKNOWN)
