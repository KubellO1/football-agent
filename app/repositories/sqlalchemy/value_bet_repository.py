"""ValueBetRepository 的 SQLAlchemy 实现。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.repositories.interfaces.value_bet_repository import ValueBetRepository
from app.repositories.sqlalchemy.mappers import ValueBetMapper
from app.repositories.sqlalchemy.models import ValueBetORM

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.entities.value_bet import ValueBet


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

    async def add_if_absent(self, entity: ValueBet) -> tuple[ValueBet, bool]:
        row = ValueBetMapper.to_orm(entity)
        statement = (
            insert(ValueBetORM)
            .values(
                id=row.id,
                fixture_id=row.fixture_id,
                selection_market=row.selection_market,
                selection_code=row.selection_code,
                selection_line=row.selection_line,
                odds_decimal=row.odds_decimal,
                bookmaker_id=row.bookmaker_id,
                model_probability=row.model_probability,
                stake_amount=row.stake_amount,
                stake_currency=row.stake_currency,
                stake_fraction=row.stake_fraction,
                confidence=row.confidence,
                rationale=row.rationale,
                created_at=row.created_at,
            )
            .on_conflict_do_nothing(index_elements=[ValueBetORM.id])
            .returning(ValueBetORM.id)
        )
        inserted_id = (await self._session.execute(statement)).scalar_one_or_none()
        persisted = await self.get(entity.id)
        if persisted is None:
            raise RuntimeError("value-bet upsert did not return a persisted row")
        return persisted, inserted_id is not None

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
