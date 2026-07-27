"""结算仓储资金账本的 PostgreSQL 集成测试。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.models.entities.settlement import BankrollEntry
from app.repositories.sqlalchemy.settlement_repository import (
    SqlAlchemyBankrollRepository,
)


@pytest.mark.integration
async def test_latest_balance_uses_stable_sequence_when_timestamps_match(
    db_session: AsyncSession,
) -> None:
    repo = SqlAlchemyBankrollRepository(db_session)
    timestamp = datetime.now(UTC)
    await repo.add(
        BankrollEntry(
            amount=Decimal("10"),
            balance_after=Decimal("110"),
            reason="first",
            created_at=timestamp,
        )
    )
    await repo.add(
        BankrollEntry(
            amount=Decimal("10"),
            balance_after=Decimal("120"),
            reason="second",
            created_at=timestamp,
        )
    )

    assert await repo.get_latest_balance() == Decimal("120")


@pytest.mark.integration
async def test_bankroll_lock_serializes_concurrent_transactions(
    db_session: AsyncSession,
) -> None:
    engine = db_session.bind
    assert isinstance(engine, AsyncEngine)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as first_session, factory() as second_session:
        first_repo = SqlAlchemyBankrollRepository(first_session)
        second_repo = SqlAlchemyBankrollRepository(second_session)

        assert await first_repo.lock_and_get_latest_balance(Decimal("100")) == Decimal("100")
        waiting = asyncio.create_task(second_repo.lock_and_get_latest_balance(Decimal("100")))
        await asyncio.sleep(0.05)
        assert not waiting.done()

        await first_session.commit()
        assert await asyncio.wait_for(waiting, timeout=1) == Decimal("100")
        await second_session.rollback()
