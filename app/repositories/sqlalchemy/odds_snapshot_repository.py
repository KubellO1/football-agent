"""OddsSnapshotRepository 的 SQLAlchemy 实现。

写入用 PostgreSQL ``INSERT ... ON CONFLICT DO NOTHING``，命中幂等唯一约束
(uq_odds_snapshots_natural) 时静默跳过，从而保证重复采集不产生重复行。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.repositories.interfaces.odds_snapshot_repository import OddsSnapshotRepository
from app.repositories.sqlalchemy.mappers import OddsSnapshotMapper
from app.repositories.sqlalchemy.models import OddsSnapshotORM

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.entities.odds_snapshot import OddsSnapshot


class SqlAlchemyOddsSnapshotRepository(OddsSnapshotRepository):
    """基于 AsyncSession 的赔率快照仓储实现。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, entity_id: UUID) -> OddsSnapshot | None:
        row = await self._session.get(OddsSnapshotORM, entity_id)
        return OddsSnapshotMapper.to_domain(row) if row is not None else None

    async def add(self, entity: OddsSnapshot) -> OddsSnapshot:
        row = OddsSnapshotMapper.to_orm(entity)
        self._session.add(row)
        await self._session.flush()
        return OddsSnapshotMapper.to_domain(row)

    async def add_if_absent(self, entity: OddsSnapshot) -> bool:
        stmt = (
            pg_insert(OddsSnapshotORM)
            .values(
                id=entity.id,
                fixture_id=entity.fixture_id,
                bookmaker_id=entity.bookmaker_id,
                selection_market=entity.selection.market.value,
                selection_code=entity.selection.code,
                selection_line=entity.selection.line,
                odds_decimal=entity.odds.decimal,
                captured_at=entity.captured_at,
                provider_source=entity.provider_source,
                provider_event_id=entity.provider_event_id,
            )
            .on_conflict_do_nothing(constraint="uq_odds_snapshots_natural")
            .returning(OddsSnapshotORM.id)
        )
        result = await self._session.execute(stmt)
        return result.first() is not None

    async def list_by_fixture(
        self,
        fixture_id: UUID,
        *,
        as_of: datetime | None = None,
    ) -> list[OddsSnapshot]:
        if as_of is not None and (as_of.tzinfo is None or as_of.utcoffset() is None):
            raise ValueError("as_of must be timezone-aware")

        stmt = select(OddsSnapshotORM).where(OddsSnapshotORM.fixture_id == fixture_id)
        if as_of is not None:
            stmt = stmt.where(OddsSnapshotORM.captured_at <= as_of)
        stmt = stmt.order_by(
            OddsSnapshotORM.captured_at,
            OddsSnapshotORM.bookmaker_id,
            OddsSnapshotORM.selection_market,
            OddsSnapshotORM.selection_code,
            OddsSnapshotORM.selection_line.asc().nulls_first(),
            OddsSnapshotORM.id,
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [OddsSnapshotMapper.to_domain(row) for row in rows]
