"""结算与追踪仓储的 SQLAlchemy 实现。"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select, text

from app.repositories.interfaces.settlement_repository import (
    BankrollRepository,
    PerformanceSnapshotRepository,
    SettlementRepository,
)
from app.repositories.sqlalchemy.mappers import (
    BankrollEntryMapper,
    PerformanceSnapshotMapper,
    SettlementMapper,
)
from app.repositories.sqlalchemy.models import (
    BankrollEntryORM,
    PerformanceSnapshotORM,
    SettlementORM,
)

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.entities.settlement import BankrollEntry, PerformanceSnapshot, Settlement

_BANKROLL_ADVISORY_LOCK_ID = 6_429_130_700_001


class SqlAlchemySettlementRepository(SettlementRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, entity_id: UUID) -> Settlement | None:
        row = await self._session.get(SettlementORM, entity_id)
        return SettlementMapper.to_domain(row) if row is not None else None

    async def get_by_value_bet(self, value_bet_id: UUID) -> Settlement | None:
        stmt = select(SettlementORM).where(SettlementORM.value_bet_id == value_bet_id).limit(1)
        row = (await self._session.execute(stmt)).scalars().first()
        return SettlementMapper.to_domain(row) if row is not None else None

    async def add(self, entity: Settlement) -> Settlement:
        row = SettlementMapper.to_orm(entity)
        self._session.add(row)
        await self._session.flush()
        return SettlementMapper.to_domain(row)

    async def list_by_fixture(self, fixture_id: UUID) -> list[Settlement]:
        stmt = select(SettlementORM).where(SettlementORM.fixture_id == fixture_id)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [SettlementMapper.to_domain(r) for r in rows]

    async def list_unsettled_value_bet_ids(self) -> list[UUID]:
        """返回 value_bets 表中尚未出现在 settlements 中的 ID。"""
        from app.repositories.sqlalchemy.models import ValueBetORM

        settled_subq = select(SettlementORM.value_bet_id)
        stmt = select(ValueBetORM.id).where(ValueBetORM.id.not_in(settled_subq))
        rows = (await self._session.execute(stmt)).scalars().all()
        return list(rows)

    async def list_between(self, start: datetime, end: datetime) -> list[Settlement]:
        stmt = select(SettlementORM).where(
            SettlementORM.settlement_timestamp >= start,
            SettlementORM.settlement_timestamp < end,
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [SettlementMapper.to_domain(r) for r in rows]

    async def list_all(self) -> list[Settlement]:
        stmt = select(SettlementORM).order_by(SettlementORM.settlement_timestamp)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [SettlementMapper.to_domain(r) for r in rows]


class SqlAlchemyBankrollRepository(BankrollRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, entity_id: UUID) -> BankrollEntry | None:
        row = await self._session.get(BankrollEntryORM, entity_id)
        return BankrollEntryMapper.to_domain(row) if row is not None else None

    async def add(self, entity: BankrollEntry) -> BankrollEntry:
        row = BankrollEntryMapper.to_orm(entity)
        self._session.add(row)
        await self._session.flush()
        return BankrollEntryMapper.to_domain(row)

    async def get_latest_balance(self) -> Decimal:
        return await self._get_latest_balance(default=Decimal("0"))

    async def lock_and_get_latest_balance(self, default: Decimal) -> Decimal:
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": _BANKROLL_ADVISORY_LOCK_ID},
        )
        return await self._get_latest_balance(default=default)

    async def _get_latest_balance(self, *, default: Decimal) -> Decimal:
        stmt = (
            select(BankrollEntryORM.balance_after)
            .order_by(BankrollEntryORM.sequence.desc())
            .limit(1)
        )
        result = (await self._session.execute(stmt)).scalar_one_or_none()
        return result if result is not None else default


class SqlAlchemyPerformanceSnapshotRepository(PerformanceSnapshotRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, entity_id: UUID) -> PerformanceSnapshot | None:
        row = await self._session.get(PerformanceSnapshotORM, entity_id)
        return PerformanceSnapshotMapper.to_domain(row) if row is not None else None

    async def add(self, entity: PerformanceSnapshot) -> PerformanceSnapshot:
        row = PerformanceSnapshotMapper.to_orm(entity)
        self._session.add(row)
        await self._session.flush()
        return PerformanceSnapshotMapper.to_domain(row)

    async def get_latest(self) -> PerformanceSnapshot | None:
        stmt = (
            select(PerformanceSnapshotORM)
            .order_by(PerformanceSnapshotORM.created_at.desc())
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalars().first()
        return PerformanceSnapshotMapper.to_domain(row) if row is not None else None
