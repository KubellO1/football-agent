"""比赛阵容快照仓储的异步 SQLAlchemy 实现。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import selectinload

from app.repositories.interfaces.lineup_repository import LineupRepository
from app.repositories.sqlalchemy.mappers import LineupMapper
from app.repositories.sqlalchemy.models import LineupORM, LineupPlayerORM

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.entities.lineup import Lineup
    from app.models.value_objects.lineup import LineupStatus


class SqlAlchemyLineupRepository(LineupRepository):
    """追加式阵容仓储；事务提交由上层 session 边界负责。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, entity_id: UUID) -> Lineup | None:
        row = await self._session.get(
            LineupORM,
            entity_id,
            options=(selectinload(LineupORM.players),),
        )
        return LineupMapper.to_domain(row) if row is not None else None

    async def add(self, entity: Lineup) -> Lineup:
        row = LineupMapper.to_orm(entity)
        self._session.add(row)
        await self._session.flush()
        return LineupMapper.to_domain(row)

    async def add_if_absent(self, entity: Lineup) -> bool:
        row = LineupMapper.to_orm(entity)
        values = {
            column.name: getattr(row, column.name)
            for column in LineupORM.__table__.columns
            if column.name not in {"created_at", "updated_at"}
        }
        stmt = (
            pg_insert(LineupORM)
            .values(**values)
            .on_conflict_do_nothing(constraint="uq_lineups_natural")
            .returning(LineupORM.id)
        )
        inserted = (await self._session.execute(stmt)).scalar_one_or_none()
        if inserted is None:
            return False

        player_values = [
            {
                "lineup_id": item.lineup_id,
                "player_id": item.player_id,
                "role": item.role,
                "ordinal": item.ordinal,
            }
            for item in row.players
        ]
        await self._session.execute(pg_insert(LineupPlayerORM).values(player_values))
        return True

    async def list_by_fixture(
        self,
        fixture_id: UUID,
        *,
        team_id: UUID | None = None,
        source: str | None = None,
        status: LineupStatus | None = None,
        as_of: datetime | None = None,
    ) -> list[Lineup]:
        self._validate_as_of(as_of)
        stmt = (
            select(LineupORM)
            .options(selectinload(LineupORM.players))
            .where(LineupORM.fixture_id == fixture_id)
        )
        if team_id is not None:
            stmt = stmt.where(LineupORM.team_id == team_id)
        if source is not None:
            stmt = stmt.where(LineupORM.source_name == source)
        if status is not None:
            stmt = stmt.where(LineupORM.status == status.value)
        if as_of is not None:
            stmt = stmt.where(LineupORM.captured_at <= as_of)
        stmt = stmt.order_by(
            LineupORM.captured_at,
            LineupORM.team_id,
            LineupORM.source_name,
            LineupORM.id,
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [LineupMapper.to_domain(row) for row in rows]

    async def get_latest_by_source(
        self,
        fixture_id: UUID,
        team_id: UUID,
        source: str,
        *,
        status: LineupStatus | None = None,
        as_of: datetime | None = None,
    ) -> Lineup | None:
        self._validate_as_of(as_of)
        stmt = (
            select(LineupORM)
            .options(selectinload(LineupORM.players))
            .where(
                LineupORM.fixture_id == fixture_id,
                LineupORM.team_id == team_id,
                LineupORM.source_name == source,
            )
            .order_by(LineupORM.captured_at.desc(), LineupORM.id.desc())
            .limit(1)
        )
        if status is not None:
            stmt = stmt.where(LineupORM.status == status.value)
        if as_of is not None:
            stmt = stmt.where(LineupORM.captured_at <= as_of)
        row = (await self._session.execute(stmt)).scalars().first()
        return LineupMapper.to_domain(row) if row is not None else None

    @staticmethod
    def _validate_as_of(as_of: datetime | None) -> None:
        if as_of is not None and (as_of.tzinfo is None or as_of.utcoffset() is None):
            raise ValueError("as_of must be timezone-aware")
