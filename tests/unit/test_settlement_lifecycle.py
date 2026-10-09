"""Settlement lifecycle, regulation-score, and fail-closed contract tests."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from app.models.entities.enums import MatchStatus
from app.models.entities.fixture import Fixture
from app.models.entities.settlement import SettlementResult
from app.models.entities.value_bet import ValueBet
from app.models.value_objects.betting import Stake, ValueEdge
from app.models.value_objects.markets import MarketType, Selection
from app.models.value_objects.money import Money
from app.models.value_objects.odds import Odds
from app.models.value_objects.probability import Probability
from app.providers.interfaces.fixtures_provider import FixturesProvider
from app.providers.schemas.fixtures import ProviderFixture, ProviderTeam
from app.repositories.interfaces.fixture_repository import FixtureRepository
from app.repositories.interfaces.settlement_repository import (
    BankrollRepository,
    SettlementRepository,
)
from app.repositories.interfaces.value_bet_repository import ValueBetRepository
from app.services.settlement import FixtureLifecycle, SettlementService


def _fixture() -> Fixture:
    return Fixture(
        competition_id=uuid4(),
        home_team_id=uuid4(),
        away_team_id=uuid4(),
        kickoff=datetime(2026, 10, 10, 18, 0, tzinfo=UTC),
        status=MatchStatus.SCHEDULED,
        external_source="api-football",
        external_id="4242",
    )


def _bet(fixture_id: UUID, code: str) -> ValueBet:
    probability = Probability(0.6)
    odds = Odds(Decimal("2.0"))
    return ValueBet(
        fixture_id=fixture_id,
        selection=Selection(market=MarketType.MATCH_RESULT, code=code),
        odds=odds,
        model_probability=probability,
        edge=ValueEdge(model_probability=probability, odds=odds),
        stake=Stake(Money(Decimal("10"), "EUR"), 0.01),
    )


def _provider_fixture(
    fixture: Fixture,
    *,
    status: str = "FT",
    regulation: tuple[int, int] | None = (1, 1),
    aggregate: tuple[int, int] | None = None,
) -> ProviderFixture:
    return ProviderFixture(
        provider_id=fixture.external_id or "",
        kickoff=fixture.kickoff,
        status=status,
        home=ProviderTeam(provider_id="1", name="Home"),
        away=ProviderTeam(provider_id="2", name="Away"),
        home_score=aggregate[0] if aggregate else None,
        away_score=aggregate[1] if aggregate else None,
        regulation_home_score=regulation[0] if regulation else None,
        regulation_away_score=regulation[1] if regulation else None,
    )


def _service(
    fixture: Fixture,
    bet: ValueBet,
    provider_fixture: ProviderFixture | None,
) -> tuple[SettlementService, AsyncMock, AsyncMock, AsyncMock]:
    provider = AsyncMock(spec=FixturesProvider)
    fixtures = AsyncMock(spec=FixtureRepository)
    value_bets = AsyncMock(spec=ValueBetRepository)
    settlements = AsyncMock(spec=SettlementRepository)
    bankroll = AsyncMock(spec=BankrollRepository)
    provider.get_fixture.return_value = provider_fixture
    fixtures.get.return_value = fixture
    value_bets.get.return_value = bet
    settlements.list_unsettled_value_bet_ids.side_effect = [[bet.id], [bet.id]]
    settlements.add.side_effect = lambda entity: entity
    bankroll.lock_and_get_latest_balance.return_value = Decimal("100")
    bankroll.add.side_effect = lambda entity: entity
    return (
        SettlementService(
            fixtures_provider=provider,
            fixtures=fixtures,
            value_bets=value_bets,
            settlements=settlements,
            bankroll=bankroll,
            initial_bankroll=Money(Decimal("100"), "EUR"),
        ),
        provider,
        settlements,
        bankroll,
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("score", "selection", "expected"),
    [
        ((1, 1), "home", SettlementResult.LOSS),
        ((1, 1), "draw", SettlementResult.WIN),
        ((1, 1), "away", SettlementResult.LOSS),
        ((0, 0), "home", SettlementResult.LOSS),
        ((0, 0), "draw", SettlementResult.WIN),
        ((0, 0), "away", SettlementResult.LOSS),
    ],
)
async def test_three_way_draw_never_pushes(
    score: tuple[int, int], selection: str, expected: SettlementResult
) -> None:
    fixture = _fixture()
    bet = _bet(fixture.id, selection)
    service, _provider, _settlements, _bankroll = _service(
        fixture,
        bet,
        _provider_fixture(fixture, regulation=score),
    )

    report = await service.settle_all()

    assert report.bets_settled == 1
    assert report.details[0].result is expected
    assert report.details[0].result is not SettlementResult.PUSH


@pytest.mark.unit
@pytest.mark.parametrize("status", ["AET", "PEN"])
async def test_aet_and_pen_use_regulation_score(status: str) -> None:
    fixture = _fixture()
    bet = _bet(fixture.id, "home")
    service, _provider, _settlements, _bankroll = _service(
        fixture,
        bet,
        _provider_fixture(fixture, status=status, regulation=(1, 1), aggregate=(3, 2)),
    )

    report = await service.settle_all()

    assert report.details[0].result is SettlementResult.LOSS
    assert (report.details[0].score_home, report.details[0].score_away) == (1, 1)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("status", "lifecycle"),
    [
        ("NS", FixtureLifecycle.NOT_STARTED),
        ("1H", FixtureLifecycle.LIVE),
        ("PST", FixtureLifecycle.POSTPONED),
        ("CANC", FixtureLifecycle.CANCELLED),
        ("ABD", FixtureLifecycle.ABANDONED),
        ("AWD", FixtureLifecycle.AWARDED_WALKOVER),
        ("WO", FixtureLifecycle.AWARDED_WALKOVER),
        ("BROKEN", FixtureLifecycle.UNKNOWN),
    ],
)
async def test_uncertain_lifecycle_defers_without_writes(
    status: str, lifecycle: FixtureLifecycle
) -> None:
    fixture = _fixture()
    bet = _bet(fixture.id, "home")
    service, _provider, settlements, bankroll = _service(
        fixture,
        bet,
        _provider_fixture(fixture, status=status),
    )

    report = await service.settle_all()

    assert report.bets_settled == 0
    assert report.bets_skipped == 1
    assert lifecycle.value in report.deferred_fixtures[str(fixture.id)]
    settlements.add.assert_not_awaited()
    bankroll.add.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.parametrize("status", ["AET", "PEN"])
async def test_aet_and_pen_without_regulation_score_fail_closed(status: str) -> None:
    fixture = _fixture()
    bet = _bet(fixture.id, "home")
    service, _provider, settlements, bankroll = _service(
        fixture,
        bet,
        _provider_fixture(fixture, status=status, regulation=None, aggregate=(3, 2)),
    )

    report = await service.settle_all()

    assert report.bets_settled == 0
    assert "regulation-time score unavailable" in report.deferred_fixtures[str(fixture.id)]
    settlements.add.assert_not_awaited()
    bankroll.add.assert_not_awaited()


@pytest.mark.unit
async def test_zero_unsettled_bets_makes_zero_provider_requests_and_writes() -> None:
    fixture = _fixture()
    bet = _bet(fixture.id, "home")
    service, provider, settlements, bankroll = _service(fixture, bet, None)
    settlements.list_unsettled_value_bet_ids.side_effect = None
    settlements.list_unsettled_value_bet_ids.return_value = []

    report = await service.settle_all()

    assert report.provider_requests == 0
    provider.get_fixture.assert_not_awaited()
    settlements.add.assert_not_awaited()
    bankroll.add.assert_not_awaited()


@pytest.mark.unit
async def test_provider_timeout_occurs_before_any_write() -> None:
    fixture = _fixture()
    bet = _bet(fixture.id, "home")
    service, provider, settlements, bankroll = _service(fixture, bet, None)
    provider.get_fixture.side_effect = TimeoutError("provider timeout")

    with pytest.raises(TimeoutError, match="provider timeout"):
        await service.settle_all()

    settlements.add.assert_not_awaited()
    bankroll.lock_and_get_latest_balance.assert_not_awaited()
    bankroll.add.assert_not_awaited()
