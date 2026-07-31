"""比赛阵容快照仓储的 PostgreSQL 集成测试。"""

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

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.entities.fixture import Fixture


async def _persist_players(
    session: AsyncSession,
    team_id: UUID,
    *,
    prefix: str,
    count: int = 14,
) -> tuple[UUID, ...]:
    repository = SqlAlchemyPlayerRepository(session)
    players = [
        await repository.add(
            Player(
                name=f"{prefix} Player {index}",
                position=PlayerPosition.GOALKEEPER if index == 0 else PlayerPosition.DEFENDER,
                team_id=team_id,
                external_source="test-provider",
                external_id=f"{prefix}-{index}",
            )
        )
        for index in range(count)
    ]
    return tuple(player.id for player in players)


def _lineup(
    fixture_id: UUID,
    team_id: UUID,
    player_ids: tuple[UUID, ...],
    captured_at: datetime,
    *,
    status: LineupStatus = LineupStatus.PREDICTED,
    source: str = "api-football",
) -> Lineup:
    return Lineup(
        fixture_id=fixture_id,
        team_id=team_id,
        status=status,
        source=LineupSource(
            name=source,
            evidence_level=EvidenceLevel.A,
            reference="https://example.com/lineup",
        ),
        starting=player_ids[:11],
        substitutes=player_ids[11:],
        formation=Formation("4-2-3-1"),
        captured_at=captured_at,
        source_updated_at=captured_at - timedelta(minutes=2),
    )


@pytest.mark.integration
async def test_add_and_get_roundtrip_preserves_ordered_lineup(
    db_session: AsyncSession,
    reference_ids: tuple[UUID, UUID, UUID],
    persisted_fixture: Fixture,
) -> None:
    player_ids = await _persist_players(db_session, reference_ids[1], prefix="roundtrip")
    repository = SqlAlchemyLineupRepository(db_session)
    entity = _lineup(
        persisted_fixture.id,
        reference_ids[1],
        player_ids,
        datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
    )

    loaded = await repository.get((await repository.add(entity)).id)

    assert loaded is not None
    assert loaded.id == entity.id
    assert loaded.starting == player_ids[:11]
    assert loaded.substitutes == player_ids[11:]
    assert loaded.formation == Formation("4-2-3-1")
    assert loaded.source == entity.source
    assert loaded.source_updated_at == entity.source_updated_at


@pytest.mark.integration
async def test_add_if_absent_uses_snapshot_natural_key(
    db_session: AsyncSession,
    reference_ids: tuple[UUID, UUID, UUID],
    persisted_fixture: Fixture,
) -> None:
    player_ids = await _persist_players(db_session, reference_ids[1], prefix="idempotent")
    repository = SqlAlchemyLineupRepository(db_session)
    captured_at = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)

    assert await repository.add_if_absent(
        _lineup(persisted_fixture.id, reference_ids[1], player_ids, captured_at)
    )
    assert not await repository.add_if_absent(
        _lineup(persisted_fixture.id, reference_ids[1], player_ids, captured_at)
    )
    assert len(await repository.list_by_fixture(persisted_fixture.id)) == 1


@pytest.mark.integration
async def test_list_by_fixture_filters_source_status_team_and_as_of(
    db_session: AsyncSession,
    reference_ids: tuple[UUID, UUID, UUID],
    persisted_fixture: Fixture,
) -> None:
    home_players = await _persist_players(db_session, reference_ids[1], prefix="filter-home")
    away_players = await _persist_players(db_session, reference_ids[2], prefix="filter-away")
    repository = SqlAlchemyLineupRepository(db_session)
    base = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    await repository.add(_lineup(persisted_fixture.id, reference_ids[1], home_players, base))
    await repository.add(
        _lineup(
            persisted_fixture.id,
            reference_ids[1],
            home_players,
            base + timedelta(minutes=10),
            status=LineupStatus.CONFIRMED,
        )
    )
    await repository.add(
        _lineup(
            persisted_fixture.id,
            reference_ids[1],
            home_players,
            base + timedelta(minutes=2),
            source="official-club",
        )
    )
    await repository.add(
        _lineup(
            persisted_fixture.id,
            reference_ids[2],
            away_players,
            base + timedelta(minutes=1),
        )
    )

    rows = await repository.list_by_fixture(
        persisted_fixture.id,
        team_id=reference_ids[1],
        source="api-football",
        status=LineupStatus.PREDICTED,
        as_of=base + timedelta(minutes=5),
    )

    assert [row.captured_at for row in rows] == [base]


@pytest.mark.integration
async def test_get_latest_by_source_respects_status_and_as_of(
    db_session: AsyncSession,
    reference_ids: tuple[UUID, UUID, UUID],
    persisted_fixture: Fixture,
) -> None:
    player_ids = await _persist_players(db_session, reference_ids[1], prefix="latest")
    repository = SqlAlchemyLineupRepository(db_session)
    base = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    await repository.add(_lineup(persisted_fixture.id, reference_ids[1], player_ids, base))
    await repository.add(
        _lineup(
            persisted_fixture.id,
            reference_ids[1],
            player_ids,
            base + timedelta(minutes=10),
            status=LineupStatus.CONFIRMED,
        )
    )

    historical = await repository.get_latest_by_source(
        persisted_fixture.id,
        reference_ids[1],
        "api-football",
        status=LineupStatus.PREDICTED,
        as_of=base + timedelta(minutes=5),
    )
    latest = await repository.get_latest_by_source(
        persisted_fixture.id,
        reference_ids[1],
        "api-football",
    )

    assert historical is not None
    assert historical.status is LineupStatus.PREDICTED
    assert latest is not None
    assert latest.status is LineupStatus.CONFIRMED


@pytest.mark.integration
async def test_queries_reject_naive_as_of(
    db_session: AsyncSession,
    persisted_fixture: Fixture,
    reference_ids: tuple[UUID, UUID, UUID],
) -> None:
    repository = SqlAlchemyLineupRepository(db_session)
    naive = datetime(2026, 8, 1, 12, 0)

    with pytest.raises(ValueError, match="as_of must be timezone-aware"):
        await repository.list_by_fixture(persisted_fixture.id, as_of=naive)
    with pytest.raises(ValueError, match="as_of must be timezone-aware"):
        await repository.get_latest_by_source(
            persisted_fixture.id,
            reference_ids[1],
            "api-football",
            as_of=naive,
        )
