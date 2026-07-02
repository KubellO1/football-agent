"""DecisionLogRepository 的 SQLAlchemy 实现。"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities.decision_log import DecisionLog
from app.repositories.interfaces.decision_log_repository import DecisionLogRepository
from app.repositories.sqlalchemy.mappers import DecisionLogMapper
from app.repositories.sqlalchemy.models import DecisionLogORM


class SqlAlchemyDecisionLogRepository(DecisionLogRepository):
    """基于 AsyncSession 的决策日志仓储实现。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, entity_id: UUID) -> DecisionLog | None:
        row = await self._session.get(DecisionLogORM, entity_id)
        return DecisionLogMapper.to_domain(row) if row is not None else None

    async def add(self, entity: DecisionLog) -> DecisionLog:
        row = DecisionLogMapper.to_orm(entity)
        self._session.add(row)
        await self._session.flush()
        return DecisionLogMapper.to_domain(row)

    async def list_by_fixture(self, fixture_id: UUID) -> list[DecisionLog]:
        stmt = (
            select(DecisionLogORM)
            .where(DecisionLogORM.fixture_id == fixture_id)
            .order_by(DecisionLogORM.created_at)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [DecisionLogMapper.to_domain(r) for r in rows]

    async def list_created_between(self, start: datetime, end: datetime) -> list[DecisionLog]:
        stmt = (
            select(DecisionLogORM)
            .where(DecisionLogORM.created_at >= start, DecisionLogORM.created_at < end)
            .order_by(DecisionLogORM.created_at)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [DecisionLogMapper.to_domain(r) for r in rows]
