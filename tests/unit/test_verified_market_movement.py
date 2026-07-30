"""VerifiedMarketMovementService 的确定性单元测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from app.models.value_objects.market_movement import MovementDirection
from app.models.value_objects.markets import MarketType, Selection
from app.models.value_objects.odds import Odds
from app.services.modeling import MarketQuote
from app.services.verified_market_movement import (
    MarketMovementStage,
    MarketMovementVerificationStatus,
    VerifiedMarketMovementService,
)
from app.services.verified_market_quote import (
    MarketQuoteIssue,
    MarketQuoteRejectionReason,
    MarketQuoteVerificationStatus,
    VerifiedMarketQuoteResult,
    VerifiedMarketQuoteService,
    VerifiedSelectionQuote,
)

OPENING_AS_OF = datetime(2026, 7, 30, 8, 0, tzinfo=UTC)
CURRENT_AS_OF = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
FIXTURE_ID = UUID("00000000-0000-0000-0000-000000000100")
BOOKMAKER_IDS = (
    UUID("00000000-0000-0000-0000-000000000001"),
    UUID("00000000-0000-0000-0000-000000000002"),
)
SNAPSHOT_IDS = (
    UUID("00000000-0000-0000-0000-000000000011"),
    UUID("00000000-0000-0000-0000-000000000012"),
)


def _selection_quote(
    code: str,
    *,
    representative_odds: str,
    consensus_odds: str,
    captured_at: datetime,
) -> VerifiedSelectionQuote:
    selection = Selection(market=MarketType.MATCH_RESULT, code=code)
    return VerifiedSelectionQuote(
        quote=MarketQuote(
            selection=selection,
            odds=Odds(Decimal(representative_odds)),
            bookmaker_id=BOOKMAKER_IDS[0],
        ),
        captured_at=captured_at,
        consensus_decimal_odds=Decimal(consensus_odds),
        contributing_snapshot_ids=SNAPSHOT_IDS,
        contributing_bookmaker_ids=BOOKMAKER_IDS,
    )


def _verified_result(
    *,
    as_of: datetime,
    representative: tuple[str, str, str],
    consensus: tuple[str, str, str],
) -> VerifiedMarketQuoteResult:
    return VerifiedMarketQuoteResult(
        status=MarketQuoteVerificationStatus.VERIFIED,
        as_of=as_of,
        quotes=tuple(
            _selection_quote(
                code,
                representative_odds=representative_odds,
                consensus_odds=consensus_odds,
                captured_at=as_of - timedelta(minutes=5),
            )
            for code, representative_odds, consensus_odds in zip(
                ("home", "draw", "away"),
                representative,
                consensus,
                strict=True,
            )
        ),
        observed_snapshot_count=6,
        eligible_snapshot_count=6,
    )


def _rejected_result(
    *,
    as_of: datetime,
    reason: MarketQuoteRejectionReason,
    selection_code: str | None = None,
) -> VerifiedMarketQuoteResult:
    return VerifiedMarketQuoteResult(
        status=MarketQuoteVerificationStatus.REJECTED,
        as_of=as_of,
        issues=(MarketQuoteIssue(reason=reason, selection_code=selection_code),),
        observed_snapshot_count=3,
    )


def _service(
    opening: VerifiedMarketQuoteResult,
    current: VerifiedMarketQuoteResult,
) -> tuple[VerifiedMarketMovementService, AsyncMock]:
    verifier = AsyncMock(spec=VerifiedMarketQuoteService)
    verifier.verify.side_effect = [opening, current]
    return VerifiedMarketMovementService(market_quotes=verifier), verifier


@pytest.mark.unit
@pytest.mark.parametrize(
    ("opening_as_of", "current_as_of", "message"),
    [
        (
            datetime(2026, 7, 30, 8, 0),
            CURRENT_AS_OF,
            "opening_as_of must be timezone-aware",
        ),
        (
            OPENING_AS_OF,
            datetime(2026, 7, 30, 12, 0),
            "current_as_of must be timezone-aware",
        ),
        (
            CURRENT_AS_OF,
            OPENING_AS_OF,
            "opening_as_of must be earlier than current_as_of",
        ),
    ],
)
async def test_compare_rejects_invalid_time_range_before_verification(
    opening_as_of: datetime,
    current_as_of: datetime,
    message: str,
) -> None:
    opening = _rejected_result(
        as_of=OPENING_AS_OF,
        reason=MarketQuoteRejectionReason.NO_SNAPSHOTS,
    )
    service, verifier = _service(opening, opening)

    with pytest.raises(ValueError, match=message):
        await service.compare(
            FIXTURE_ID,
            opening_as_of=opening_as_of,
            current_as_of=current_as_of,
        )

    verifier.verify.assert_not_awaited()


@pytest.mark.unit
async def test_compare_uses_consensus_odds_and_preserves_provenance() -> None:
    opening = _verified_result(
        as_of=OPENING_AS_OF,
        representative=("2.10", "3.20", "3.80"),
        consensus=("2.00", "3.20", "4.00"),
    )
    current = _verified_result(
        as_of=CURRENT_AS_OF,
        representative=("1.90", "3.20", "4.30"),
        consensus=("1.80", "3.20", "4.40"),
    )
    service, verifier = _service(opening, current)

    result = await service.compare(
        FIXTURE_ID,
        opening_as_of=OPENING_AS_OF,
        current_as_of=CURRENT_AS_OF,
    )

    assert result.status is MarketMovementVerificationStatus.VERIFIED
    assert result.accepted is True
    assert [item.selection.code for item in result.movements] == ["home", "draw", "away"]
    assert [item.movement.direction for item in result.movements] == [
        MovementDirection.SHORTENING,
        MovementDirection.STABLE,
        MovementDirection.DRIFTING,
    ]
    assert result.movements[0].movement.decimal_delta == pytest.approx(-0.2)
    assert result.movements[0].movement.opening.decimal == Decimal("2.00")
    assert result.movements[0].movement.current.decimal == Decimal("1.80")
    assert result.movements[0].opening_quote.contributing_snapshot_ids == SNAPSHOT_IDS
    assert result.movements[0].current_quote.contributing_bookmaker_ids == BOOKMAKER_IDS
    assert verifier.verify.await_args_list[0].kwargs == {"as_of": OPENING_AS_OF}
    assert verifier.verify.await_args_list[1].kwargs == {"as_of": CURRENT_AS_OF}


@pytest.mark.unit
async def test_compare_fails_closed_and_marks_opening_issue() -> None:
    opening = _rejected_result(
        as_of=OPENING_AS_OF,
        reason=MarketQuoteRejectionReason.MISSING_SELECTION,
        selection_code="draw",
    )
    current = _verified_result(
        as_of=CURRENT_AS_OF,
        representative=("2.00", "3.20", "4.00"),
        consensus=("2.00", "3.20", "4.00"),
    )
    service, _ = _service(opening, current)

    result = await service.compare(
        FIXTURE_ID,
        opening_as_of=OPENING_AS_OF,
        current_as_of=CURRENT_AS_OF,
    )

    assert result.status is MarketMovementVerificationStatus.REJECTED
    assert result.accepted is False
    assert result.movements == ()
    assert result.issues[0].stage is MarketMovementStage.OPENING
    assert result.issues[0].reason is MarketQuoteRejectionReason.MISSING_SELECTION
    assert result.issues[0].selection_code == "draw"


@pytest.mark.unit
async def test_compare_collects_issues_from_both_stages() -> None:
    opening = _rejected_result(
        as_of=OPENING_AS_OF,
        reason=MarketQuoteRejectionReason.NO_SNAPSHOTS,
    )
    current = _rejected_result(
        as_of=CURRENT_AS_OF,
        reason=MarketQuoteRejectionReason.STALE_SELECTION,
        selection_code="away",
    )
    service, verifier = _service(opening, current)

    result = await service.compare(
        FIXTURE_ID,
        opening_as_of=OPENING_AS_OF,
        current_as_of=CURRENT_AS_OF,
    )

    assert result.movements == ()
    assert [(issue.stage, issue.reason, issue.selection_code) for issue in result.issues] == [
        (MarketMovementStage.OPENING, MarketQuoteRejectionReason.NO_SNAPSHOTS, None),
        (MarketMovementStage.CURRENT, MarketQuoteRejectionReason.STALE_SELECTION, "away"),
    ]
    assert verifier.verify.await_count == 2
