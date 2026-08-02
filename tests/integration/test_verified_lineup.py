"""VerifiedLineupService 的 PostgreSQL as-of 集成测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from app.models.entities.enums import PlayerPosition
from app.models.entities.lineup import Lineup
from app.models.entities.player import Player
from app.models.value_objects.decision import EvidenceLevel
from app.models.value_objects.lineup import Formation, LineupSource, LineupStatus
from app.repositories.sqlalchemy.lineup_repository import SqlAlchemyLineupRepository
from app.repositories.sqlalchemy.player_repository import SqlAlchemyPlayerRepository
from app.services.verified_lineup import VerifiedLineupService

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.entities.fixture import Fixture


async def _players(
    session: AsyncSession,
    team_id: UUID,
    *,
    prefix: str,
) -> tuple[UUID, ...]:
    repository = SqlAlchemyPlayerRepository(session)
    entities = [
        await repository.add(
            Player(
                name=f"{prefix} Player {index}",
                position=PlayerPosition.GOALKEEPER if index == 0 else PlayerPosition.DEFENDER,
                team_id=team_id,
                external_source="test-provider",
                external_id=f"{prefix}-{index}",
            )
        )
        for index in range(14)
    ]
    return tuple(player.id for player in entities)


def _lineup(
    fixture_id: UUID,
    team_id: UUID,
    players: tuple[UUID, ...],
    captured_at: datetime,
    *,
    status: LineupStatus,
) -> Lineup:
    return Lineup(
        fixture_id=fixture_id,
        team_id=team_id,
        status=status,
        source=LineupSource(
            name="api-football",
            evidence_level=EvidenceLevel.A,
            reference="/fixtures/lineups?fixture=integration-test",
        ),
        starting=players[:11],
        substitutes=players[11:],
        formation=Formation("4-2-3-1"),
        captured_at=captured_at,
    )


@pytest.mark.integration
async def test_selects_latest_confirmed_lineups_visible_at_analysis_time(
    db_session: AsyncSession,
    persisted_fixture: Fixture,
    reference_ids: tuple[UUID, UUID, UUID],
) -> None:
    repository = SqlAlchemyLineupRepository(db_session)
    service = VerifiedLineupService(repository=repository)
    home_players = await _players(db_session, reference_ids[1], prefix="verified-home")
    away_players = await _players(db_session, reference_ids[2], prefix="verified-away")
    base = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)

    await repository.add(
        _lineup(
            persisted_fixture.id,
            reference_ids[1],
            home_players,
            base - timedelta(minutes=5),
            status=LineupStatus.PREDICTED,
        )
    )
    visible_home = await repository.add(
        _lineup(
            persisted_fixture.id,
            reference_ids[1],
            home_players,
            base,
            status=LineupStatus.CONFIRMED,
        )
    )
    await repository.add(
        _lineup(
            persisted_fixture.id,
            reference_ids[1],
            home_players,
            base + timedelta(minutes=10),
            status=LineupStatus.CONFIRMED,
        )
    )
    visible_away = await repository.add(
        _lineup(
            persisted_fixture.id,
            reference_ids[2],
            away_players,
            base + timedelta(minutes=1),
            status=LineupStatus.CONFIRMED,
        )
    )

    result = await service.verify(
        persisted_fixture,
        as_of=base + timedelta(minutes=5),
    )

    assert result.accepted
    assert result.home.lineup is not None
    assert result.home.lineup.id == visible_home.id
    assert result.away.lineup is not None
    assert result.away.lineup.id == visible_away.id
