"""FixtureRepository 的 SQLAlchemy 实现。"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities.fixture import Fixture
from app.repositories.interfaces.fixture_repository import FixtureRepository
from app.repositories.sqlalchemy.mappers import FixtureMapper
from app.repositories.sqlalchemy.models import FixtureORM


class SqlAlchemyFixtureRepository(FixtureRepository):
    """基于 AsyncSession 的比赛仓储实现。

    事务边界由上层的 Database.session() 上下文统一管理，这里只做读写，
    add 后 flush 以便拿到持久化状态，不在此提交。
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, entity_id: UUID) -> Fixture | None:
        row = await self._session.get(FixtureORM, entity_id)
        return FixtureMapper.to_domain(row) if row is not None else None

    async def add(self, entity: Fixture) -> Fixture:
        row = FixtureMapper.to_orm(entity)
        self._session.add(row)
        await self._session.flush()
        return FixtureMapper.to_domain(row)

    async def list_by_kickoff_window(self, start: datetime, end: datetime) -> list[Fixture]:
        stmt = (
            select(FixtureORM)
            .where(FixtureORM.kickoff >= start, FixtureORM.kickoff < end)
            .order_by(FixtureORM.kickoff)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [FixtureMapper.to_domain(r) for r in rows]

    async def list_by_competition(self, competition_id: UUID) -> list[Fixture]:
        stmt = (
            select(FixtureORM)
            .where(FixtureORM.competition_id == competition_id)
            .order_by(FixtureORM.kickoff)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [FixtureMapper.to_domain(r) for r in rows]
