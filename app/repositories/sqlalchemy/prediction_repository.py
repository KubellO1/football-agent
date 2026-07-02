"""PredictionRepository 的 SQLAlchemy 实现。"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities.prediction import MatchPrediction
from app.repositories.interfaces.prediction_repository import PredictionRepository
from app.repositories.sqlalchemy.mappers import PredictionMapper
from app.repositories.sqlalchemy.models import PredictionORM


class SqlAlchemyPredictionRepository(PredictionRepository):
    """基于 AsyncSession 的预测仓储实现。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, entity_id: UUID) -> MatchPrediction | None:
        row = await self._session.get(PredictionORM, entity_id)
        return PredictionMapper.to_domain(row) if row is not None else None

    async def add(self, entity: MatchPrediction) -> MatchPrediction:
        row = PredictionMapper.to_orm(entity)
        self._session.add(row)
        await self._session.flush()
        return PredictionMapper.to_domain(row)

    async def get_by_fixture(self, fixture_id: UUID) -> MatchPrediction | None:
        # 取该场比赛最新一条预测（按生成时间倒序）
        stmt = (
            select(PredictionORM)
            .where(PredictionORM.fixture_id == fixture_id)
            .order_by(PredictionORM.generated_at.desc())
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalars().first()
        return PredictionMapper.to_domain(row) if row is not None else None
