"""球队阵容球员身份采集服务的 PostgreSQL 集成测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.core.exceptions import ExternalServiceError, NotFoundError, ValidationError
from app.models.entities.enums import PlayerPosition
from app.models.entities.player import Player
from app.providers.interfaces.player_squad_provider import PlayerSquadProvider
from app.providers.schemas.player_squad import ProviderSquadBatch, ProviderSquadPlayer
from app.repositories.sqlalchemy.models import PlayerORM, TeamORM
from app.repositories.sqlalchemy.player_repository import SqlAlchemyPlayerRepository
from app.repositories.sqlalchemy.reference_repositories import SqlAlchemyTeamRepository
from app.services.player_squad_ingestion import PlayerSquadIngestionService

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

SOURCE = "api-football"
CAPTURED_AT = datetime(2026, 7, 31, 10, 0, tzinfo=UTC)


class FakePlayerSquadProvider(PlayerSquadProvider):
    def __init__(self, batch: ProviderSquadBatch) -> None:
        self._batch = batch
        self.requested_team_ids: list[str] = []

    async def get_team_squad(
        self,
        *,
        team_external_id: str,
    ) -> ProviderSquadBatch:
        self.requested_team_ids.append(team_external_id)
        return self._batch


def _record(
    *,
    player_external_id: str = "player-1",
    player_name: str = "Test Player",
    raw_position: str = "Attacker",
) -> ProviderSquadPlayer:
    return ProviderSquadPlayer(
        player_external_id=player_external_id,
        player_name=player_name,
        raw_position=raw_position,
    )


def _batch(
    *,
    records: list[ProviderSquadPlayer] | None = None,
    source: str = SOURCE,
    team_external_id: str = "team-1",
    response_complete: bool = True,
) -> ProviderSquadBatch:
    return ProviderSquadBatch(
        source=source,
        team_external_id=team_external_id,
        captured_at=CAPTURED_AT,
        response_complete=response_complete,
        records=records or [],
        request_reference="/players/squads?team=team-1",
    )


async def _seed_team(
    session: AsyncSession,
    *,
    external_id: str = "team-1",
) -> UUID:
    team_id = uuid4()
    session.add(
        TeamORM(
            id=team_id,
            name="Test Team",
            external_source=SOURCE,
            external_id=external_id,
        ),
    )
    await session.flush()
    return team_id


def _service(
    session: AsyncSession,
    batch: ProviderSquadBatch,
) -> tuple[PlayerSquadIngestionService, FakePlayerSquadProvider]:
    provider = FakePlayerSquadProvider(batch)
    return (
        PlayerSquadIngestionService(
            provider=provider,
            teams=SqlAlchemyTeamRepository(session),
            players=SqlAlchemyPlayerRepository(session),
            source=SOURCE,
        ),
        provider,
    )


async def _count_players(session: AsyncSession) -> int:
    return (await session.execute(select(func.count()).select_from(PlayerORM))).scalar_one()


@pytest.mark.integration
async def test_sync_creates_verified_players_and_maps_positions(
    db_session: AsyncSession,
) -> None:
    team_id = await _seed_team(db_session)
    records = [
        _record(player_external_id="gk", raw_position="Goalkeeper"),
        _record(player_external_id="def", raw_position="Defender"),
        _record(player_external_id="mid", raw_position="Midfielder"),
        _record(player_external_id="fwd", raw_position="Attacker"),
    ]
    service, provider = _service(db_session, _batch(records=records))

    report = await service.sync_team(team_external_id=" team-1 ")

    assert provider.requested_team_ids == ["team-1"]
    assert report.records_received == 4
    assert report.records_created == 4
    assert report.records_updated == 0
    assert report.records_unchanged == 0
    players = await SqlAlchemyPlayerRepository(db_session).list_by_team(team_id)
    assert {player.external_id: player.position for player in players} == {
        "gk": PlayerPosition.GOALKEEPER,
        "def": PlayerPosition.DEFENDER,
        "mid": PlayerPosition.MIDFIELDER,
        "fwd": PlayerPosition.FORWARD,
    }


@pytest.mark.integration
async def test_sync_is_idempotent_for_unchanged_squad(
    db_session: AsyncSession,
) -> None:
    await _seed_team(db_session)
    service, _ = _service(db_session, _batch(records=[_record()]))

    first = await service.sync_team(team_external_id="team-1")
    second = await service.sync_team(team_external_id="team-1")

    assert first.records_created == 1
    assert second.records_created == 0
    assert second.records_updated == 0
    assert second.records_unchanged == 1
    assert await _count_players(db_session) == 1


@pytest.mark.integration
async def test_sync_updates_mutable_player_master_data(
    db_session: AsyncSession,
) -> None:
    team_id = await _seed_team(db_session)
    other_team_id = await _seed_team(db_session, external_id="team-other")
    repository = SqlAlchemyPlayerRepository(db_session)
    existing = await repository.add(
        Player(
            name="Old Name",
            position=PlayerPosition.DEFENDER,
            team_id=other_team_id,
            external_source=SOURCE,
            external_id="player-1",
        ),
    )
    service, _ = _service(
        db_session,
        _batch(records=[_record(player_name="New Name", raw_position="Forward")]),
    )

    report = await service.sync_team(team_external_id="team-1")

    updated = await repository.get(existing.id)
    assert updated is not None
    assert report.records_updated == 1
    assert updated.name == "New Name"
    assert updated.position is PlayerPosition.FORWARD
    assert updated.team_id == team_id
    assert updated.external_id == "player-1"
    assert updated.external_source == SOURCE


@pytest.mark.integration
async def test_unknown_team_fails_before_provider_call(
    db_session: AsyncSession,
) -> None:
    service, provider = _service(db_session, _batch(records=[_record()]))

    with pytest.raises(NotFoundError, match="team-1"):
        await service.sync_team(team_external_id="team-1")

    assert provider.requested_team_ids == []
    assert await _count_players(db_session) == 0


@pytest.mark.integration
@pytest.mark.parametrize(
    ("batch", "error_type", "message"),
    [
        (_batch(source="other"), ValidationError, "source mismatch"),
        (_batch(team_external_id="other"), ValidationError, "does not match request"),
        (
            _batch(response_complete=False),
            ExternalServiceError,
            "response is incomplete",
        ),
    ],
)
async def test_invalid_batch_fails_without_writes(
    db_session: AsyncSession,
    batch: ProviderSquadBatch,
    error_type: type[Exception],
    message: str,
) -> None:
    await _seed_team(db_session)
    service, _ = _service(db_session, batch)

    with pytest.raises(error_type, match=message):
        await service.sync_team(team_external_id="team-1")

    assert await _count_players(db_session) == 0


@pytest.mark.integration
async def test_unknown_position_rejects_entire_batch_without_writes(
    db_session: AsyncSession,
) -> None:
    await _seed_team(db_session)
    records = [
        _record(player_external_id="valid"),
        _record(player_external_id="invalid", raw_position="Utility"),
    ]
    service, _ = _service(db_session, _batch(records=records))

    with pytest.raises(ValidationError, match="unsupported player position"):
        await service.sync_team(team_external_id="team-1")

    assert await _count_players(db_session) == 0


@pytest.mark.integration
async def test_duplicate_player_ids_reject_entire_batch_without_writes(
    db_session: AsyncSession,
) -> None:
    await _seed_team(db_session)
    records = [_record(), _record(player_name="Duplicate")]
    service, _ = _service(db_session, _batch(records=records))

    with pytest.raises(ValidationError, match="duplicate player ids"):
        await service.sync_team(team_external_id="team-1")

    assert await _count_players(db_session) == 0
