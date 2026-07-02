"""ValueBetRepository 的 SQLAlchemy 实现。"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities.value_bet import ValueBet
from app.repositories.interfaces.value_bet_repository import ValueBetRepository
from app.repositories.sqlalchemy.mappers import ValueBetMapper
from app.repositories.sqlalchemy.models import ValueBetORM


class SqlAlchemyValueBetRepository(ValueBetRepository):
    """基于 AsyncSession 的推荐仓储实现。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, entity_id: UUID) -> ValueBet | None:
        row = await self._session.get(ValueBetORM, entity_id)
        return ValueBetMapper.to_domain(row) if row is not None else None

    async def add(self, entity: ValueBet) -> ValueBet:
        row = ValueBetMapper.to_orm(entity)
        self._session.add(row)
        await self._session.flush()
        return ValueBetMapper.to_domain(row)

    async def list_by_fixture(self, fixture_id: UUID) -> list[ValueBet]:
        stmt = select(ValueBetORM).where(ValueBetORM.fixture_id == fixture_id)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [ValueBetMapper.to_domain(r) for r in rows]

    async def list_created_between(self, start: datetime, end: datetime) -> list[ValueBet]:
        stmt = (
            select(ValueBetORM)
            .where(ValueBetORM.created_at >= start, ValueBetORM.created_at < end)
            .order_by(ValueBetORM.created_at)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [ValueBetMapper.to_domain(r) for r in rows]
