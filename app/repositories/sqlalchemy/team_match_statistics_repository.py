"""TeamMatchStatisticsRepository 的异步 SQLAlchemy 实现。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.repositories.interfaces.team_match_statistics_repository import (
    TeamMatchStatisticsRepository,
)
from app.repositories.sqlalchemy.mappers import TeamMatchStatisticsMapper
from app.repositories.sqlalchemy.models import TeamMatchStatisticsORM

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.entities.team_match_statistics import TeamMatchStatistics


class SqlAlchemyTeamMatchStatisticsRepository(TeamMatchStatisticsRepository):
    """追加式统计快照仓储；事务提交由上层 session 边界负责。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, entity_id: UUID) -> TeamMatchStatistics | None:
        row = await self._session.get(TeamMatchStatisticsORM, entity_id)
        return TeamMatchStatisticsMapper.to_domain(row) if row is not None else None

    async def add(self, entity: TeamMatchStatistics) -> TeamMatchStatistics:
        row = TeamMatchStatisticsMapper.to_orm(entity)
        self._session.add(row)
        await self._session.flush()
        return TeamMatchStatisticsMapper.to_domain(row)

    async def add_if_absent(self, entity: TeamMatchStatistics) -> bool:
        row = TeamMatchStatisticsMapper.to_orm(entity)
        values = {
            column.name: getattr(row, column.name)
            for column in TeamMatchStatisticsORM.__table__.columns
            if column.name not in {"created_at", "updated_at"}
        }
        stmt = (
            pg_insert(TeamMatchStatisticsORM)
            .values(**values)
            .on_conflict_do_nothing(constraint="uq_team_match_statistics_natural")
            .returning(TeamMatchStatisticsORM.id)
        )
        result = await self._session.execute(stmt)
        return result.first() is not None

    async def list_by_fixture(
        self,
        fixture_id: UUID,
        *,
        team_id: UUID | None = None,
        source: str | None = None,
        as_of: datetime | None = None,
    ) -> list[TeamMatchStatistics]:
        stmt = select(TeamMatchStatisticsORM).where(TeamMatchStatisticsORM.fixture_id == fixture_id)
        if team_id is not None:
            stmt = stmt.where(TeamMatchStatisticsORM.team_id == team_id)
        if source is not None:
            stmt = stmt.where(TeamMatchStatisticsORM.source == source)
        if as_of is not None:
            stmt = stmt.where(TeamMatchStatisticsORM.captured_at <= as_of)
        stmt = stmt.order_by(
            TeamMatchStatisticsORM.captured_at,
            TeamMatchStatisticsORM.team_id,
            TeamMatchStatisticsORM.source,
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [TeamMatchStatisticsMapper.to_domain(row) for row in rows]

    async def get_latest_by_source(
        self,
        fixture_id: UUID,
        team_id: UUID,
        source: str,
        *,
        as_of: datetime | None = None,
    ) -> TeamMatchStatistics | None:
        stmt = (
            select(TeamMatchStatisticsORM)
            .where(
                TeamMatchStatisticsORM.fixture_id == fixture_id,
                TeamMatchStatisticsORM.team_id == team_id,
                TeamMatchStatisticsORM.source == source,
            )
            .order_by(TeamMatchStatisticsORM.captured_at.desc())
            .limit(1)
        )
        if as_of is not None:
            stmt = stmt.where(TeamMatchStatisticsORM.captured_at <= as_of)
        row = (await self._session.execute(stmt)).scalars().first()
        return TeamMatchStatisticsMapper.to_domain(row) if row is not None else None
