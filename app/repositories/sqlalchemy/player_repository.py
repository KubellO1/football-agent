"""Player Repository 的异步 SQLAlchemy 实现。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from app.repositories.interfaces.player_repository import PlayerRepository
from app.repositories.sqlalchemy.mappers import PlayerMapper
from app.repositories.sqlalchemy.models import PlayerORM

if TYPE_CHECKING:
    from collections.abc import Iterable
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.entities.player import Player


class SqlAlchemyPlayerRepository(PlayerRepository):
    """Player 主数据仓储；事务提交由上层 session 边界负责。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, entity_id: UUID) -> Player | None:
        row = await self._session.get(PlayerORM, entity_id)
        return PlayerMapper.to_domain(row) if row is not None else None

    async def add(self, entity: Player) -> Player:
        row = PlayerMapper.to_orm(entity)
        self._session.add(row)
        await self._session.flush()
        return PlayerMapper.to_domain(row)

    async def get_by_external_id(
        self,
        source: str,
        external_id: str,
    ) -> Player | None:
        stmt = (
            select(PlayerORM)
            .where(
                PlayerORM.external_source == source,
                PlayerORM.external_id == external_id,
            )
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalars().first()
        return PlayerMapper.to_domain(row) if row is not None else None

    async def list_by_team(self, team_id: UUID) -> list[Player]:
        stmt = (
            select(PlayerORM)
            .where(PlayerORM.team_id == team_id)
            .order_by(PlayerORM.name, PlayerORM.id)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [PlayerMapper.to_domain(row) for row in rows]

    async def list_by_ids(self, ids: Iterable[UUID]) -> list[Player]:
        id_list = list(ids)
        if not id_list:
            return []
        stmt = (
            select(PlayerORM)
            .where(PlayerORM.id.in_(id_list))
            .order_by(PlayerORM.name, PlayerORM.id)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [PlayerMapper.to_domain(row) for row in rows]

    async def update(self, entity: Player) -> Player:
        row = await self._session.get(PlayerORM, entity.id)
        if row is None:
            raise KeyError(f"player {entity.id} not found for update")
        identity_changes = {
            "external_source": (row.external_source, entity.external_source),
            "external_id": (row.external_id, entity.external_id),
        }
        changed_fields = [
            name for name, (stored, requested) in identity_changes.items() if stored != requested
        ]
        if changed_fields:
            names = ", ".join(changed_fields)
            raise ValueError(f"player identity fields cannot change: {names}")

        row.name = entity.name
        row.position = entity.position.value
        row.team_id = entity.team_id
        row.date_of_birth = entity.date_of_birth
        await self._session.flush()
        return PlayerMapper.to_domain(row)
