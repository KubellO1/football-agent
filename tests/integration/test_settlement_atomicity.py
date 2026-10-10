"""Settlement replay, concurrency, and transaction rollback on PostgreSQL."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.models.entities.enums import MatchStatus
from app.models.entities.fixture import Fixture
from app.models.entities.value_bet import ValueBet
from app.models.value_objects.betting import Stake, ValueEdge
from app.models.value_objects.markets import MarketType, Selection
from app.models.value_objects.money import Money
from app.models.value_objects.odds import Odds
from app.models.value_objects.probability import Probability
from app.providers.interfaces.fixtures_provider import FixturesProvider
from app.providers.schemas.fixtures import ProviderFixture, ProviderTeam
from app.repositories.interfaces.settlement_repository import BankrollRepository
from app.repositories.sqlalchemy.fixture_repository import SqlAlchemyFixtureRepository
from app.repositories.sqlalchemy.models import BankrollEntryORM, SettlementORM
from app.repositories.sqlalchemy.settlement_repository import (
    SqlAlchemyBankrollRepository,
    SqlAlchemySettlementRepository,
)
from app.repositories.sqlalchemy.value_bet_repository import SqlAlchemyValueBetRepository
from app.services.settlement import AuthoritativeVoidEvidence, SettlementService

if TYPE_CHECKING:
    from uuid import UUID


async def _seed_candidate(
    session: AsyncSession,
    reference_ids: tuple[UUID, UUID, UUID],
) -> tuple[Fixture, ValueBet, ProviderFixture]:
    competition_id, home_id, away_id = reference_ids
    fixture = await SqlAlchemyFixtureRepository(session).add(
        Fixture(
            competition_id=competition_id,
            home_team_id=home_id,
            away_team_id=away_id,
            kickoff=datetime(2026, 10, 9, 18, 0, tzinfo=UTC),
            status=MatchStatus.SCHEDULED,
            external_source="api-football",
            external_id="98765",
        )
    )
    probability = Probability(0.6)
    odds = Odds(Decimal("2.0"))
    value_bet = await SqlAlchemyValueBetRepository(session).add(
        ValueBet(
            fixture_id=fixture.id,
            selection=Selection(market=MarketType.MATCH_RESULT, code="home"),
            odds=odds,
            model_probability=probability,
            edge=ValueEdge(model_probability=probability, odds=odds),
            stake=Stake(Money(Decimal("10"), "EUR"), 0.01),
        )
    )
    provider_fixture = ProviderFixture(
        provider_id="98765",
        kickoff=fixture.kickoff,
        status="FT",
        home=ProviderTeam(provider_id="1", name="Home"),
        away=ProviderTeam(provider_id="2", name="Away"),
        regulation_home_score=2,
        regulation_away_score=1,
    )
    return fixture, value_bet, provider_fixture


def _service(
    session: AsyncSession,
    provider_fixture: ProviderFixture,
    *,
    bankroll: BankrollRepository | None = None,
    void_evidence: AuthoritativeVoidEvidence | None = None,
) -> SettlementService:
    provider = AsyncMock(spec=FixturesProvider)
    provider.get_fixture.return_value = provider_fixture
    evidence_provider = None
    if void_evidence is not None:
        evidence_provider = AsyncMock()
        evidence_provider.get_void_evidence.return_value = void_evidence
    return SettlementService(
        fixtures_provider=provider,
        fixtures=SqlAlchemyFixtureRepository(session),
        value_bets=SqlAlchemyValueBetRepository(session),
        settlements=SqlAlchemySettlementRepository(session),
        bankroll=bankroll or SqlAlchemyBankrollRepository(session),
        initial_bankroll=Money(Decimal("100"), "EUR"),
        void_evidence_provider=evidence_provider,
    )


async def _counts(session: AsyncSession) -> tuple[int, int]:
    settlements = (
        await session.execute(select(func.count()).select_from(SettlementORM))
    ).scalar_one()
    bankroll = (
        await session.execute(select(func.count()).select_from(BankrollEntryORM))
    ).scalar_one()
    return settlements, bankroll


@pytest.mark.integration
async def test_settlement_replay_and_concurrent_invocation_insert_once(
    db_session: AsyncSession,
    reference_ids: tuple[UUID, UUID, UUID],
) -> None:
    _fixture, _value_bet, provider_fixture = await _seed_candidate(db_session, reference_ids)
    await db_session.commit()
    engine = db_session.bind
    assert isinstance(engine, AsyncEngine)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def run() -> int:
        async with factory.begin() as session:
            return (await _service(session, provider_fixture).settle_all()).bets_settled

    first_pair = await asyncio.gather(run(), run())
    assert sorted(first_pair) == [0, 1]
    assert await run() == 0
    async with factory() as verify:
        assert await _counts(verify) == (1, 1)


@pytest.mark.integration
async def test_bankroll_failure_rolls_back_settlement(
    db_session: AsyncSession,
    reference_ids: tuple[UUID, UUID, UUID],
) -> None:
    _fixture, _value_bet, provider_fixture = await _seed_candidate(db_session, reference_ids)
    await db_session.commit()
    engine = db_session.bind
    assert isinstance(engine, AsyncEngine)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        bankroll = AsyncMock(spec=BankrollRepository)
        bankroll.lock_and_get_latest_balance.return_value = Decimal("100")
        bankroll.add.side_effect = RuntimeError("simulated bankroll failure")
        with pytest.raises(RuntimeError, match="simulated bankroll failure"):
            async with session.begin():
                await _service(session, provider_fixture, bankroll=bankroll).settle_all()

    async with factory() as verify:
        assert await _counts(verify) == (0, 0)


@pytest.mark.integration
async def test_void_replay_and_concurrent_invocation_insert_once(
    db_session: AsyncSession,
    reference_ids: tuple[UUID, UUID, UUID],
) -> None:
    fixture, value_bet, _provider_fixture = await _seed_candidate(db_session, reference_ids)
    await db_session.commit()
    cancelled_fixture = ProviderFixture(
        provider_id="98765",
        kickoff=fixture.kickoff,
        status="CANC",
        home=ProviderTeam(provider_id="1", name="Home"),
        away=ProviderTeam(provider_id="2", name="Away"),
    )
    evidence = AuthoritativeVoidEvidence(
        value_bet_id=value_bet.id,
        fixture_id=fixture.id,
        reason_code="BOOKMAKER_CANCELLED_MARKET",
        source="bookmaker-settlement-feed",
        evidence_reference="settlement-notice-123",
        bookmaker_id=value_bet.bookmaker_id,
    )
    engine = db_session.bind
    assert isinstance(engine, AsyncEngine)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def run() -> int:
        async with factory.begin() as session:
            return (
                await _service(
                    session,
                    cancelled_fixture,
                    void_evidence=evidence,
                ).settle_all()
            ).bets_settled

    first_pair = await asyncio.gather(run(), run())
    assert sorted(first_pair) == [0, 1]
    assert await run() == 0

    async with factory() as verify:
        assert await _counts(verify) == (1, 1)
        settlement = (await verify.execute(select(SettlementORM))).scalar_one()
        entry = (await verify.execute(select(BankrollEntryORM))).scalar_one()
        assert settlement.result == "V"
        assert settlement.score_home is None
        assert settlement.score_away is None
        assert settlement.profit_loss == Decimal("0")
        assert settlement.void_reason_code == "BOOKMAKER_CANCELLED_MARKET"
        assert settlement.bankroll_before == settlement.bankroll_after == Decimal("100")
        assert entry.amount == Decimal("0")
        assert entry.balance_after == Decimal("100")


@pytest.mark.integration
async def test_void_bankroll_failure_rolls_back_terminal_settlement(
    db_session: AsyncSession,
    reference_ids: tuple[UUID, UUID, UUID],
) -> None:
    fixture, value_bet, _provider_fixture = await _seed_candidate(db_session, reference_ids)
    await db_session.commit()
    cancelled_fixture = ProviderFixture(
        provider_id="98765",
        kickoff=fixture.kickoff,
        status="CANC",
        home=ProviderTeam(provider_id="1", name="Home"),
        away=ProviderTeam(provider_id="2", name="Away"),
    )
    evidence = AuthoritativeVoidEvidence(
        value_bet_id=value_bet.id,
        fixture_id=fixture.id,
        reason_code="BOOKMAKER_CANCELLED_MARKET",
        source="bookmaker-settlement-feed",
        evidence_reference="settlement-notice-123",
        bookmaker_id=value_bet.bookmaker_id,
    )
    engine = db_session.bind
    assert isinstance(engine, AsyncEngine)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    bankroll = AsyncMock(spec=BankrollRepository)
    bankroll.lock_and_get_latest_balance.return_value = Decimal("100")
    bankroll.add.side_effect = RuntimeError("simulated VOID bankroll failure")

    async with factory() as session:
        with pytest.raises(RuntimeError, match="VOID bankroll failure"):
            async with session.begin():
                await _service(
                    session,
                    cancelled_fixture,
                    bankroll=bankroll,
                    void_evidence=evidence,
                ).settle_all()

    async with factory() as verify:
        assert await _counts(verify) == (0, 0)
