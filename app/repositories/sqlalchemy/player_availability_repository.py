"""球员可用性观察仓储的异步 SQLAlchemy 实现。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.repositories.interfaces.player_availability_repository import (
    PlayerAvailabilityObservationRepository,
)
from app.repositories.sqlalchemy.mappers import PlayerAvailabilityObservationMapper
from app.repositories.sqlalchemy.models import PlayerAvailabilityObservationORM

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.entities.player_availability import PlayerAvailabilityObservation


class SqlAlchemyPlayerAvailabilityObservationRepository(
    PlayerAvailabilityObservationRepository,
):
    """追加式可用性观察仓储；事务提交由上层 session 边界负责。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, entity_id: UUID) -> PlayerAvailabilityObservation | None:
        row = await self._session.get(PlayerAvailabilityObservationORM, entity_id)
        return PlayerAvailabilityObservationMapper.to_domain(row) if row is not None else None

    async def add(
        self,
        entity: PlayerAvailabilityObservation,
    ) -> PlayerAvailabilityObservation:
        row = PlayerAvailabilityObservationMapper.to_orm(entity)
        self._session.add(row)
        await self._session.flush()
        return PlayerAvailabilityObservationMapper.to_domain(row)

    async def add_if_absent(self, entity: PlayerAvailabilityObservation) -> bool:
        row = PlayerAvailabilityObservationMapper.to_orm(entity)
        values = {
            column.name: getattr(row, column.name)
            for column in PlayerAvailabilityObservationORM.__table__.columns
            if column.name not in {"created_at", "updated_at"}
        }
        stmt = (
            pg_insert(PlayerAvailabilityObservationORM)
            .values(**values)
            .on_conflict_do_nothing(
                constraint="uq_player_availability_observations_natural",
            )
            .returning(PlayerAvailabilityObservationORM.id)
        )
        result = await self._session.execute(stmt)
        return result.first() is not None

    async def list_by_fixture(
        self,
        fixture_id: UUID,
        *,
        team_id: UUID | None = None,
        player_id: UUID | None = None,
        source: str | None = None,
        as_of: datetime | None = None,
    ) -> list[PlayerAvailabilityObservation]:
        self._validate_as_of(as_of)
        stmt = select(PlayerAvailabilityObservationORM).where(
            PlayerAvailabilityObservationORM.fixture_id == fixture_id,
        )
        if team_id is not None:
            stmt = stmt.where(PlayerAvailabilityObservationORM.team_id == team_id)
        if player_id is not None:
            stmt = stmt.where(PlayerAvailabilityObservationORM.player_id == player_id)
        if source is not None:
            stmt = stmt.where(PlayerAvailabilityObservationORM.source_name == source)
        if as_of is not None:
            stmt = stmt.where(PlayerAvailabilityObservationORM.captured_at <= as_of)
        stmt = stmt.order_by(
            PlayerAvailabilityObservationORM.captured_at,
            PlayerAvailabilityObservationORM.player_id,
            PlayerAvailabilityObservationORM.source_name,
            PlayerAvailabilityObservationORM.id,
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [PlayerAvailabilityObservationMapper.to_domain(row) for row in rows]

    async def get_latest_by_source(
        self,
        fixture_id: UUID,
        player_id: UUID,
        source: str,
        *,
        as_of: datetime | None = None,
    ) -> PlayerAvailabilityObservation | None:
        self._validate_as_of(as_of)
        stmt = (
            select(PlayerAvailabilityObservationORM)
            .where(
                PlayerAvailabilityObservationORM.fixture_id == fixture_id,
                PlayerAvailabilityObservationORM.player_id == player_id,
                PlayerAvailabilityObservationORM.source_name == source,
            )
            .order_by(
                PlayerAvailabilityObservationORM.captured_at.desc(),
                PlayerAvailabilityObservationORM.id.desc(),
            )
            .limit(1)
        )
        if as_of is not None:
            stmt = stmt.where(PlayerAvailabilityObservationORM.captured_at <= as_of)
        row = (await self._session.execute(stmt)).scalars().first()
        return PlayerAvailabilityObservationMapper.to_domain(row) if row is not None else None

    @staticmethod
    def _validate_as_of(as_of: datetime | None) -> None:
        if as_of is not None and (as_of.tzinfo is None or as_of.utcoffset() is None):
            raise ValueError("as_of must be timezone-aware")
