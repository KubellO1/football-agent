"""VerifiedMarketQuoteService 的确定性单元测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.models.entities.odds_snapshot import OddsSnapshot
from app.models.value_objects.markets import MarketType, Selection
from app.models.value_objects.odds import Odds
from app.repositories.interfaces.odds_snapshot_repository import OddsSnapshotRepository
from app.services.verified_market_quote import (
    MarketQuoteRejectionReason,
    MarketQuoteVerificationStatus,
    VerifiedMarketQuotePolicy,
    VerifiedMarketQuoteService,
)

AS_OF = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
FIXTURE_ID = UUID("00000000-0000-0000-0000-000000000100")
BOOKMAKER_IDS = (
    UUID("00000000-0000-0000-0000-000000000001"),
    UUID("00000000-0000-0000-0000-000000000002"),
    UUID("00000000-0000-0000-0000-000000000003"),
)


class InMemoryOddsSnapshotRepository(OddsSnapshotRepository):
    def __init__(self, snapshots: list[OddsSnapshot] | None = None) -> None:
        self.snapshots = snapshots or []
        self.requested_as_of: datetime | None = None
        self.list_called = False

    async def get(self, entity_id: UUID) -> OddsSnapshot | None:
        return next((item for item in self.snapshots if item.id == entity_id), None)

    async def add(self, entity: OddsSnapshot) -> OddsSnapshot:
        self.snapshots.append(entity)
        return entity

    async def add_if_absent(self, entity: OddsSnapshot) -> bool:
        if entity in self.snapshots:
            return False
        self.snapshots.append(entity)
        return True

    async def list_by_fixture(
        self,
        fixture_id: UUID,
        *,
        as_of: datetime | None = None,
    ) -> list[OddsSnapshot]:
        self.list_called = True
        self.requested_as_of = as_of
        return [item for item in self.snapshots if item.fixture_id == fixture_id]


def _snapshot(
    code: str,
    bookmaker_id: UUID,
    decimal_odds: str,
    *,
    captured_at: datetime = AS_OF - timedelta(minutes=5),
    market: MarketType = MarketType.MATCH_RESULT,
) -> OddsSnapshot:
    return OddsSnapshot(
        id=uuid4(),
        fixture_id=FIXTURE_ID,
        bookmaker_id=bookmaker_id,
        selection=Selection(market=market, code=code),
        odds=Odds(Decimal(decimal_odds)),
        captured_at=captured_at,
    )


def _service(
    snapshots: list[OddsSnapshot],
    *,
    maximum_age: timedelta = timedelta(minutes=30),
    minimum_bookmakers: int = 2,
    maximum_relative_deviation: float = 0.2,
) -> tuple[VerifiedMarketQuoteService, InMemoryOddsSnapshotRepository]:
    repository = InMemoryOddsSnapshotRepository(snapshots)
    return (
        VerifiedMarketQuoteService(
            repository=repository,
            policy=VerifiedMarketQuotePolicy(
                maximum_age=maximum_age,
                minimum_bookmakers=minimum_bookmakers,
                maximum_relative_deviation=maximum_relative_deviation,
            ),
        ),
        repository,
    )


def _complete_market() -> list[OddsSnapshot]:
    prices = {
        "home": ("2.00", "2.10", "9.00"),
        "draw": ("3.20", "3.30", "12.00"),
        "away": ("4.00", "4.20", "15.00"),
    }
    return [
        _snapshot(code, bookmaker_id, price)
        for code, selection_prices in prices.items()
        for bookmaker_id, price in zip(BOOKMAKER_IDS, selection_prices, strict=True)
    ]


@pytest.mark.unit
def test_policy_rejects_unsafe_boundaries() -> None:
    with pytest.raises(ValueError, match="maximum_age must be positive"):
        VerifiedMarketQuotePolicy(maximum_age=timedelta(0))
    with pytest.raises(ValueError, match="minimum_bookmakers"):
        VerifiedMarketQuotePolicy(maximum_age=timedelta(minutes=1), minimum_bookmakers=1)
    with pytest.raises(ValueError, match="maximum_relative_deviation"):
        VerifiedMarketQuotePolicy(
            maximum_age=timedelta(minutes=1),
            maximum_relative_deviation=1.0,
        )


@pytest.mark.unit
async def test_verify_rejects_naive_as_of_before_repository_access() -> None:
    service, repository = _service([])

    with pytest.raises(ValueError, match="as_of must be timezone-aware"):
        await service.verify(FIXTURE_ID, as_of=datetime(2026, 7, 30, 12, 0))

    assert repository.list_called is False


@pytest.mark.unit
async def test_verify_returns_not_found_for_empty_repository() -> None:
    service, repository = _service([])

    result = await service.verify(FIXTURE_ID, as_of=AS_OF)

    assert result.status is MarketQuoteVerificationStatus.NOT_FOUND
    assert result.accepted is False
    assert result.market_quotes == ()
    assert result.issues[0].reason is MarketQuoteRejectionReason.NO_SNAPSHOTS
    assert repository.requested_as_of == AS_OF


@pytest.mark.unit
async def test_verify_rejects_repository_future_leakage() -> None:
    service, _ = _service(
        [_snapshot("home", BOOKMAKER_IDS[0], "2.00", captured_at=AS_OF + timedelta(seconds=1))]
    )

    with pytest.raises(ValueError, match="later than as_of"):
        await service.verify(FIXTURE_ID, as_of=AS_OF)


@pytest.mark.unit
async def test_verify_builds_complete_market_from_real_representative_quotes() -> None:
    snapshots = _complete_market()
    older = _snapshot(
        "home",
        BOOKMAKER_IDS[1],
        "1.20",
        captured_at=AS_OF - timedelta(minutes=10),
    )
    snapshots.append(older)
    service, _ = _service(snapshots)

    result = await service.verify(FIXTURE_ID, as_of=AS_OF)

    assert result.status is MarketQuoteVerificationStatus.VERIFIED
    assert result.accepted is True
    assert [item.quote.selection.code for item in result.quotes] == ["home", "draw", "away"]
    assert [item.quote.odds.decimal for item in result.quotes] == [
        Decimal("2.00"),
        Decimal("3.20"),
        Decimal("4.00"),
    ]
    assert [item.consensus_decimal_odds for item in result.quotes] == [
        Decimal("2.05"),
        Decimal("3.25"),
        Decimal("4.10"),
    ]
    assert all(item.quote.bookmaker_id == BOOKMAKER_IDS[0] for item in result.quotes)
    assert all(len(item.contributing_bookmaker_ids) == 2 for item in result.quotes)
    assert older.id not in result.quotes[0].contributing_snapshot_ids


@pytest.mark.unit
async def test_verify_fails_closed_when_selection_is_missing() -> None:
    snapshots = [snapshot for snapshot in _complete_market() if snapshot.selection.code != "draw"]
    service, _ = _service(snapshots)

    result = await service.verify(FIXTURE_ID, as_of=AS_OF)

    assert result.status is MarketQuoteVerificationStatus.REJECTED
    assert result.quotes == ()
    assert len(result.issues) == 1
    assert result.issues[0].reason is MarketQuoteRejectionReason.MISSING_SELECTION
    assert result.issues[0].selection_code == "draw"


@pytest.mark.unit
async def test_verify_rejects_stale_selection_relative_to_as_of() -> None:
    snapshots = _complete_market()
    for snapshot in snapshots:
        snapshot.captured_at = AS_OF - timedelta(hours=2)
    service, _ = _service(snapshots, maximum_age=timedelta(minutes=30))

    result = await service.verify(FIXTURE_ID, as_of=AS_OF)

    assert result.status is MarketQuoteVerificationStatus.REJECTED
    assert result.quotes == ()
    assert [issue.reason for issue in result.issues] == [
        MarketQuoteRejectionReason.STALE_SELECTION,
        MarketQuoteRejectionReason.STALE_SELECTION,
        MarketQuoteRejectionReason.STALE_SELECTION,
    ]


@pytest.mark.unit
async def test_verify_rejects_insufficient_bookmakers_after_outlier_filter() -> None:
    snapshots = [
        _snapshot(code, bookmaker_id, price)
        for code in ("home", "draw", "away")
        for bookmaker_id, price in zip(
            BOOKMAKER_IDS,
            ("2.00", "8.00", "9.00"),
            strict=True,
        )
    ]
    service, _ = _service(
        snapshots,
        minimum_bookmakers=2,
        maximum_relative_deviation=0.1,
    )

    result = await service.verify(FIXTURE_ID, as_of=AS_OF)

    assert result.status is MarketQuoteVerificationStatus.REJECTED
    assert result.quotes == ()
    assert all(
        issue.reason is MarketQuoteRejectionReason.INSUFFICIENT_BOOKMAKERS
        for issue in result.issues
    )
