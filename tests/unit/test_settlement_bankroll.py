"""结算服务资金余额语义的回归测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from app.models.entities.enums import MatchStatus
from app.models.entities.fixture import Fixture
from app.models.entities.value_bet import ValueBet
from app.models.value_objects.betting import Stake, ValueEdge
from app.models.value_objects.markets import MarketType, Selection
from app.models.value_objects.money import Money
from app.models.value_objects.odds import Odds
from app.models.value_objects.probability import Probability
from app.models.value_objects.score import Score
from app.providers.interfaces.fixtures_provider import FixturesProvider
from app.providers.schemas.fixtures import ProviderFixture, ProviderTeam
from app.repositories.interfaces.fixture_repository import FixtureRepository
from app.repositories.interfaces.settlement_repository import (
    BankrollRepository,
    SettlementRepository,
)
from app.repositories.interfaces.value_bet_repository import ValueBetRepository
from app.services.settlement import SettlementService


def _value_bet(fixture_id: UUID, selection: Selection) -> ValueBet:
    probability = Probability(0.6)
    odds = Odds(Decimal("2.0"))
    return ValueBet(
        fixture_id=fixture_id,
        selection=selection,
        odds=odds,
        model_probability=probability,
        edge=ValueEdge(model_probability=probability, odds=odds),
        stake=Stake(
            amount=Money(Decimal("10"), "EUR"),
            fraction_of_bankroll=0.1,
        ),
    )


@pytest.mark.unit
async def test_settlement_uses_initial_bankroll_and_rolls_balance_forward() -> None:
    fixture = Fixture(
        competition_id=uuid4(),
        home_team_id=uuid4(),
        away_team_id=uuid4(),
        kickoff=datetime.now(UTC),
        status=MatchStatus.FINISHED,
        score=Score(home=2, away=1),
        external_id="1001",
        external_source="api-football",
    )
    bets = [
        _value_bet(
            fixture.id,
            Selection(market=MarketType.MATCH_RESULT, code="home"),
        ),
        _value_bet(
            fixture.id,
            Selection(market=MarketType.BOTH_TEAMS_TO_SCORE, code="yes"),
        ),
    ]

    fixtures = AsyncMock(spec=FixtureRepository)
    value_bets = AsyncMock(spec=ValueBetRepository)
    settlements = AsyncMock(spec=SettlementRepository)
    bankroll = AsyncMock(spec=BankrollRepository)
    provider = AsyncMock(spec=FixturesProvider)
    call_order: list[str] = []
    fixtures.get.return_value = fixture
    value_bets.get.side_effect = lambda value_bet_id: next(
        bet for bet in bets if bet.id == value_bet_id
    )
    settlements.list_unsettled_value_bet_ids.side_effect = [
        [bet.id for bet in bets],
        [bet.id for bet in bets],
    ]
    provider.get_fixture.return_value = ProviderFixture(
        provider_id="1001",
        kickoff=fixture.kickoff,
        status="FT",
        home=ProviderTeam(provider_id="1", name="Home"),
        away=ProviderTeam(provider_id="2", name="Away"),
        regulation_home_score=2,
        regulation_away_score=1,
    )
    settlements.add.side_effect = lambda entity: entity
    bankroll.lock_and_get_latest_balance.side_effect = lambda _default: (
        call_order.append("lock"),
        Decimal("100"),
    )[1]
    bankroll.add.side_effect = lambda entity: entity

    service = SettlementService(
        fixtures_provider=provider,
        fixtures=fixtures,
        value_bets=value_bets,
        settlements=settlements,
        bankroll=bankroll,
        initial_bankroll=Money(Decimal("100"), "EUR"),
    )

    report = await service.settle_all()

    assert call_order == ["lock"]
    assert settlements.list_unsettled_value_bet_ids.await_count == 2
    bankroll.lock_and_get_latest_balance.assert_awaited_once_with(Decimal("100"))
    saved_settlements = [call.args[0] for call in settlements.add.await_args_list]
    assert [(saved.bankroll_before, saved.bankroll_after) for saved in saved_settlements] == [
        (Decimal("100"), Decimal("110.0")),
        (Decimal("110.0"), Decimal("120.0")),
    ]
    assert report.total_pl == Decimal("20.0")
