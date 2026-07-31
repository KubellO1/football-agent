"""Player Repository 的 PostgreSQL 集成测试。"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from app.models.entities.enums import PlayerPosition
from app.models.entities.player import Player
from app.repositories.sqlalchemy.player_repository import SqlAlchemyPlayerRepository

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession


def _player(
    name: str,
    team_id: UUID | None,
    external_id: str,
) -> Player:
    return Player(
        name=name,
        position=PlayerPosition.MIDFIELDER,
        team_id=team_id,
        date_of_birth=date(1998, 4, 12),
        external_source="test-provider",
        external_id=external_id,
    )


@pytest.mark.integration
async def test_add_get_and_external_lookup_roundtrip(
    db_session: AsyncSession,
    reference_ids: tuple[UUID, UUID, UUID],
) -> None:
    repository = SqlAlchemyPlayerRepository(db_session)
    entity = _player("Alpha Player", reference_ids[1], "player-100")

    saved = await repository.add(entity)
    loaded = await repository.get(saved.id)
    by_external_id = await repository.get_by_external_id(
        "test-provider",
        "player-100",
    )

    assert loaded is not None
    assert loaded.id == entity.id
    assert loaded.name == "Alpha Player"
    assert loaded.position is PlayerPosition.MIDFIELDER
    assert loaded.team_id == reference_ids[1]
    assert loaded.date_of_birth == date(1998, 4, 12)
    assert loaded.external_source == "test-provider"
    assert loaded.external_id == "player-100"
    assert by_external_id == loaded


@pytest.mark.integration
async def test_list_queries_filter_and_sort_players(
    db_session: AsyncSession,
    reference_ids: tuple[UUID, UUID, UUID],
) -> None:
    repository = SqlAlchemyPlayerRepository(db_session)
    bravo = await repository.add(
        _player("Bravo Player", reference_ids[1], "player-200"),
    )
    alpha = await repository.add(
        _player("Alpha Player", reference_ids[1], "player-201"),
    )
    away = await repository.add(
        _player("Away Player", reference_ids[2], "player-202"),
    )

    by_team = await repository.list_by_team(reference_ids[1])
    by_ids = await repository.list_by_ids([bravo.id, away.id])

    assert [player.id for player in by_team] == [alpha.id, bravo.id]
    assert [player.id for player in by_ids] == [away.id, bravo.id]
    assert await repository.list_by_ids([]) == []


@pytest.mark.integration
async def test_update_changes_mutable_player_data(
    db_session: AsyncSession,
    reference_ids: tuple[UUID, UUID, UUID],
) -> None:
    repository = SqlAlchemyPlayerRepository(db_session)
    saved = await repository.add(
        _player("Original Name", reference_ids[1], "player-300"),
    )
    changed = Player(
        id=saved.id,
        name="Updated Name",
        position=PlayerPosition.FORWARD,
        team_id=reference_ids[2],
        date_of_birth=date(1998, 4, 13),
        external_source=saved.external_source,
        external_id=saved.external_id,
    )

    updated = await repository.update(changed)

    assert updated.name == "Updated Name"
    assert updated.position is PlayerPosition.FORWARD
    assert updated.team_id == reference_ids[2]
    assert updated.date_of_birth == date(1998, 4, 13)
    assert updated.external_source == "test-provider"
    assert updated.external_id == "player-300"


@pytest.mark.integration
async def test_update_rejects_external_identity_changes(
    db_session: AsyncSession,
    reference_ids: tuple[UUID, UUID, UUID],
) -> None:
    repository = SqlAlchemyPlayerRepository(db_session)
    saved = await repository.add(
        _player("Original Name", reference_ids[1], "player-400"),
    )
    conflicting = Player(
        id=saved.id,
        name=saved.name,
        position=saved.position,
        team_id=saved.team_id,
        date_of_birth=saved.date_of_birth,
        external_source="other-provider",
        external_id="other-player",
    )

    with pytest.raises(ValueError) as exc_info:
        await repository.update(conflicting)

    message = str(exc_info.value)
    assert "external_source" in message
    assert "external_id" in message


@pytest.mark.integration
async def test_update_rejects_missing_player(
    db_session: AsyncSession,
) -> None:
    repository = SqlAlchemyPlayerRepository(db_session)
    missing = _player("Missing Player", None, "player-missing")
    missing.id = uuid4()

    with pytest.raises(KeyError, match="not found for update"):
        await repository.update(missing)
