"""PostgreSQL concurrency contract for deterministic value-bet IDs."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.models.entities.value_bet import ValueBet
from app.models.value_objects.betting import Stake, ValueEdge
from app.models.value_objects.markets import MarketType, Selection
from app.models.value_objects.money import Money
from app.models.value_objects.odds import Odds
from app.models.value_objects.probability import Probability
from app.repositories.sqlalchemy.models import ValueBetORM
from app.repositories.sqlalchemy.value_bet_repository import SqlAlchemyValueBetRepository


@pytest.mark.integration
async def test_concurrent_value_bet_replay_inserts_once(
    db_session: AsyncSession,
    persisted_fixture,
) -> None:  # type: ignore[no-untyped-def]
    identity = uuid4()
    probability = Probability(0.6)
    odds = Odds(Decimal("2.1"))
    value_bet = ValueBet(
        id=identity,
        fixture_id=persisted_fixture.id,
        selection=Selection(market=MarketType.MATCH_RESULT, code="home"),
        odds=odds,
        model_probability=probability,
        edge=ValueEdge(model_probability=probability, odds=odds),
        stake=Stake(Money(Decimal("10"), "EUR"), 0.01),
        created_at=datetime(2026, 10, 10, 12, 0, tzinfo=UTC),
    )
    await db_session.commit()
    engine = db_session.bind
    assert isinstance(engine, AsyncEngine)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def insert_once() -> bool:
        async with factory.begin() as session:
            _saved, inserted = await SqlAlchemyValueBetRepository(session).add_if_absent(value_bet)
            return inserted

    inserted = await asyncio.gather(insert_once(), insert_once())

    assert sorted(inserted) == [False, True]
    async with factory() as verify:
        count = (
            await verify.execute(
                select(func.count()).select_from(ValueBetORM).where(ValueBetORM.id == identity)
            )
        ).scalar_one()
    assert count == 1
